#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import re
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin
from datetime import datetime
import zipfile
import shutil

class HTMLContentExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.in_script = False
        self.in_style = False
        self.in_meta = False
        self.in_nav = False
        self.in_aside = False
        self.in_article = False
        self.article_content = []
        self.depth = 0
        # UI 和导航相关的关键词
        self.ui_keywords = [
            '下載 App', '所有看板', '即時熱門看板', '創作者排行榜',
            '最近造訪', '查看全部', '追蹤的看板', '查看所有文章',
            'Dcard 精選看板', '服務條款', '幫助中心', '品牌識別',
            '徵才', '商業合作', '隱私政策', '看板首頁', '回到頂部'
        ]

    def handle_starttag(self, tag, attrs):
        if tag in ['script', 'style']:
            self.__dict__[f'in_{tag}'] = True
        elif tag == 'nav':
            self.in_nav = True
        elif tag == 'aside':
            self.in_aside = True
        elif tag == 'article':
            self.in_article = True
        elif tag == 'main':
            self.in_article = True

    def handle_endtag(self, tag):
        if tag in ['script', 'style']:
            self.__dict__[f'in_{tag}'] = False
        elif tag == 'nav':
            self.in_nav = False
        elif tag == 'aside':
            self.in_aside = False
        elif tag in ['article', 'main']:
            self.in_article = False
        elif tag == 'br' and not self.in_nav and not self.in_aside:
            self.text_parts.append('\n')
        elif tag == 'p' and not self.in_nav and not self.in_aside:
            self.text_parts.append('\n')

    def handle_data(self, data):
        if not self.in_script and not self.in_style and not self.in_nav and not self.in_aside:
            text = data.strip()
            if text and not any(ui_kw in text for ui_kw in self.ui_keywords):
                self.text_parts.append(text)

    def get_text(self):
        # 连接文本并进行清理
        full_text = '\n'.join(self.text_parts).strip()

        # 再次过滤UI元素
        lines = full_text.split('\n')
        cleaned_lines = []
        for line in lines:
            if not any(ui_kw in line for ui_kw in self.ui_keywords) and line.strip():
                cleaned_lines.append(line)

        return '\n'.join(cleaned_lines).strip()

def extract_meta(html_content):
    """提取 meta 標籤資訊"""
    meta = {}
    patterns = {
        'og:url': r'<meta\s+property=["\']og:url["\']\s+content=["\']([^"\']+)["\']',
        'og:title': r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        'canonical': r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']'
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, html_content, re.IGNORECASE)
        if match:
            meta[key] = match.group(1)
    return meta

def extract_domain(url):
    """從 URL 提取域名"""
    if not url:
        return None
    parsed = urlparse(url)
    return parsed.netloc if parsed.netloc else None

def obscure_personal_info(text):
    """遮蔽個資：學號、Email、電話"""
    if not text:
        return text, []

    changes = []

    # 遮蔽學號（通常 8-10 位數字或混合）
    student_id_pattern = r'\b\d{8,10}\b'
    if re.search(student_id_pattern, text):
        text = re.sub(student_id_pattern, lambda m: m.group(0)[:2] + '***' + m.group(0)[-2:], text)
        changes.append('student_id')

    # 遮蔽 Email
    email_pattern = r'[\w.-]+@[\w.-]+\.\w+'
    if re.search(email_pattern, text):
        text = re.sub(email_pattern, lambda m: m.group(0)[:2] + '***@' + m.group(0).split('@')[1], text)
        changes.append('email')

    # 遮蔽電話（格式多樣）
    phone_pattern = r'(?:\d{2,4}[-.\s]?)*\d{4}[-.\s]?\d{4}'
    if re.search(phone_pattern, text):
        text = re.sub(phone_pattern, lambda m: m.group(0)[:2] + '***' + m.group(0)[-3:], text)
        changes.append('phone')

    return text, changes

def classify_topic(title, content):
    """根據標題和內容分類主題"""
    combined = (title + content).lower()

    keywords = {
        '研究生壓力': ['研究所', '碩士', '碩班', '研究', '考研', '研究室'],
        '心理健康與情緒': ['焦慮', '壓力', '崩潰', '憂鬱', '失眠', '哭', '心理', '情緒'],
        '人際與孤獨': ['人際', '孤獨', '朋友', '交友', '孤獨', '寂寞', '人緣'],
        '教育決策（休學/轉學/輔系）': ['休學', '轉學', '輔系', '退學', '放棄'],
        '校園社群與數位壓力': ['宿舍', '社群', '社團', '校園', '校隊'],
        '求職與生涯規劃': ['求職', '工作', '職涯', '就業', '面試', '畢業', '找工作']
    }

    scores = {}
    for category, words in keywords.items():
        score = sum(combined.count(word) for word in words)
        scores[category] = score

    primary = max(scores, key=scores.get) if scores else '其他'

    # 次要分類
    secondary = [k for k, v in scores.items() if v > 0 and k != primary][:3]

    return primary, secondary

def extract_links(html_content):
    """提取 HTML 中的所有超連結"""
    links = []
    pattern = r'<a\s+(?:[^>]*?\s+)?href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>'
    matches = re.finditer(pattern, html_content, re.IGNORECASE)
    for match in matches:
        href = match.group(1).strip()
        text = match.group(2).strip()
        if href:
            links.append({'href': href, 'text': text if text else None})
    return links

def detect_crisis_flags(content):
    """檢測危機徵候"""
    flags = []
    crisis_keywords = ['自殺', '自傷', '自我傷害', '死', '消失', '放棄', '無法活', '想死']
    harassment_keywords = ['騷擾', '侵害', '暴力', '欺凌', '霸凌']
    medical_keywords = ['醫生', '藥物', '治療', '診斷', '症狀']

    content_lower = content.lower()

    if any(kw in content_lower for kw in crisis_keywords):
        flags.append('crisis')
    if any(kw in content_lower for kw in harassment_keywords):
        flags.append('harassment')
    if any(kw in content_lower for kw in medical_keywords):
        flags.append('medical')

    return flags

def generate_mock_dialogue(title, content, has_crisis):
    """生成模擬對話"""
    dialogue = []

    if has_crisis:
        # 危機回應
        dialogue.append({
            "role": "user",
            "text": title if title else "我最近遇到了很困擾的問題"
        })
        dialogue.append({
            "role": "assistant",
            "text": "我聽到你的困擾。如果你現在感到非常難受，請聯絡學校心理諮商服務或撥打各地心理健康支持專線。我們可以一起討論如何度過這段艱難時期。"
        })
    else:
        dialogue.append({
            "role": "user",
            "text": title if title else "我最近感到很困擾"
        })
        dialogue.append({
            "role": "assistant",
            "text": "謝謝你的分享。我理解這對你來說可能很挑戰。能否告訴我更多細節，讓我更好地理解你的情況？"
        })

    # 添加用戶反應
    content_excerpt = content[:150] if len(content) > 150 else content
    dialogue.append({
        "role": "user",
        "text": f"嗯，主要是因為{content_excerpt}..."
    })

    dialogue.append({
        "role": "assistant",
        "text": "我理解這些挑戰確實會帶來壓力。許多學生都有類似的經歷。你可以考慮和朋友、家人或專業輔導員談論這些感受。"
    })

    return dialogue

def parse_html_file(html_path):
    """解析單個 HTML 檔案"""
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except:
        try:
            with open(html_path, 'r', encoding='big5') as f:
                html_content = f.read()
        except:
            with open(html_path, 'r', encoding='latin-1') as f:
                html_content = f.read()

    # 提取基本信息
    meta = extract_meta(html_content)
    url = meta.get('og:url') or meta.get('canonical')
    domain = extract_domain(url)

    # 提取文本內容
    extractor = HTMLContentExtractor()
    try:
        extractor.feed(html_content)
        content_text = extractor.get_text()
    except:
        content_text = ""

    # 提取標題
    title_match = re.search(r'<title>([^<]+)</title>', html_content, re.IGNORECASE)
    title = title_match.group(1) if title_match else None
    if title and ' _ Dcard' in title:
        title = title.replace(' _ Dcard', '').replace(' - 看板', '').strip()

    # 提取作者和時間（根據平台）
    author_display = None
    timestamp_iso = None
    board_or_category = None

    # PTT 格式
    if 'ptt' in html_content.lower():
        board_match = re.search(r'看板\s+([^\s]+)', html_content)
        if board_match:
            board_or_category = board_match.group(1)
        author_match = re.search(r'作者.*?href[^>]*>([^<]+)</a>', html_content, re.IGNORECASE | re.DOTALL)
        if author_match:
            author_display = author_match.group(1).strip()

    # Dcard 格式
    elif 'dcard' in html_content.lower():
        board_match = re.search(r'板 _ Dcard', html_content)
        if board_match:
            if '心情板' in html_content:
                board_or_category = '心情'
            elif '研究所板' in html_content:
                board_or_category = '研究所'
            elif '心理板' in html_content:
                board_or_category = '心理'

    # 遮蔽個資
    content_text, obscured = obscure_personal_info(content_text)

    # 分類
    primary_topic, secondary_topics = classify_topic(title or '', content_text)

    # 提取連結
    links = extract_links(html_content)

    # 檢測危機
    crisis_flags = detect_crisis_flags(content_text)
    has_crisis = 'crisis' in crisis_flags

    # 生成摘要
    summary = content_text[:180].replace('\n', ' ').strip()
    if len(content_text) > 180:
        summary += '...'

    # 生成模擬對話
    dialogue = generate_mock_dialogue(title, content_text, has_crisis)

    # 建立 JSON 物件
    result = {
        "source_file": os.path.basename(html_path),
        "url": url,
        "domain": domain,
        "board_or_category": board_or_category,
        "title": title,
        "author_display": author_display,
        "timestamp_iso": timestamp_iso,
        "language": "zh-Hant",
        "content_text": content_text[:2000] if content_text else None,  # 限制長度
        "links_extracted": links,
        "topic_primary": primary_topic,
        "topic_secondary": secondary_topics,
        "summary_150zh": summary,
        "moderation_flags": crisis_flags,
        "dialogue_mock": dialogue,
        "notes": f"個資遮蔽: {', '.join(obscured)}" if obscured else ""
    }

    return result

def generate_json_filename(source_file, domain):
    """生成 JSON 檔名"""
    # 去除副檔名
    base_name = Path(source_file).stem
    # 保留 domain，否則用 'dcard' 或 'ptt'
    domain_short = 'ptt' if domain and 'ptt' in domain else ('dcard' if domain and 'dcard' in domain else 'unknown')
    # 轉換為小寫、去除空格、用底線替代
    slug = re.sub(r'[^\w\-]', '_', base_name.lower())
    slug = re.sub(r'_+', '_', slug).strip('_')
    return f"{domain_short}__{slug}__.json"

def main():
    input_dir = Path('./input_html')
    output_dir = Path('./output_jsons')

    # 檢查輸入目錄
    if not input_dir.exists():
        input_dir.mkdir(parents=True)
        print(f"建立 {input_dir}，但為空。")
        return

    # 清理輸出目錄
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    # 找所有 HTML 檔案（先在根目錄，再在 input_html）
    html_files = []
    html_files.extend(Path('.').glob('*.html'))
    html_files.extend(Path('.').glob('*.htm'))

    if not html_files:
        print(f"找不到 HTML 檔案。")
        return

    print(f"發現 {len(html_files)} 個 HTML 檔案。處理中...")

    for idx, html_file in enumerate(sorted(html_files), 1):
        try:
            result = parse_html_file(str(html_file))
            json_filename = generate_json_filename(result['source_file'], result['domain'])
            json_path = output_dir / json_filename

            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            if idx % 20 == 0:
                print(f"progress: {idx}/{len(html_files)}")
        except Exception as e:
            print(f"error: {html_file.name} - {str(e)}")

    # 壓縮
    zip_path = Path('./nycu_articles_json.zip')
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for json_file in output_dir.glob('*.json'):
            zipf.write(json_file, arcname=json_file.name)

    print(f"progress: {len(html_files)}/{len(html_files)}")
    print(f"files: ./output_jsons/")
    print(f"zip: ./nycu_articles_json.zip")

if __name__ == '__main__':
    main()
