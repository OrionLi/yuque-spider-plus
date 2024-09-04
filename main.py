import argparse
import json
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3 import Retry


def fetch_url_title(url, cookies=None):
    try:
        headers = {'Cookie': cookies} if cookies else {}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            html_content = response.text
            soup = BeautifulSoup(html_content, 'html.parser')
            title_tag = soup.title
            if title_tag:
                title = title_tag.string.strip()
                # 替换非法字符
                title_cleaned = title.replace('/', '-').replace('\\', '-').replace(':', '-').replace('*', '-').replace(
                    '?', '-').replace('"', '-').replace('<', '-').replace('>', '-').replace('|', '-')
                # 去除固定的字符串
                title_cleaned = title_cleaned.replace(' · 语雀', '')
                # 提取链接中的部分并按指定格式拼接到标题中
                match = re.search(r'u\d+/([\w-]+)', url)
                if match:
                    extracted_part = match.group(1)  # 获取第一个捕获组的内容
                    final_title = f"{extracted_part}-{title_cleaned}"
                    print("页面标题:", final_title)
                    return final_title
                else:
                    return title_cleaned
            else:
                return "无标题"
        else:
            print(f"请求失败，状态码：{response.status_code}")
            return "请求失败"
    except requests.exceptions.RequestException as e:
        print(f"请求发生错误：{e}")
        return "请求错误"


def save_page(book_id, slug, path, cookies=None):
    try:
        headers = {'Cookie': cookies} if cookies else {}
        docsdata = requests.get(
            f'https://www.yuque.com/api/docs/{slug}?book_id={book_id}&merge_dynamic_data=false&mode=markdown',
            headers=headers, timeout=10
        )
        if docsdata.status_code != 200:
            print("文档下载失败 页面可能被删除 ", book_id, slug, docsdata.content)
            return
        docsjson = json.loads(docsdata.content)
        markdown_content = docsjson['data']['sourcecode']

        assets_dir = os.path.join(os.path.dirname(path), 'assets')
        if not os.path.exists(assets_dir):
            os.makedirs(assets_dir)

        def download_image(match):
            url = match.group(1)
            if not url.startswith('http'):
                return match.group(0)
            url = url.split('#')[0]  # 移除URL中的所有参数
            timestamp = int(time.time() * 1000)
            extension = os.path.splitext(url)[1]
            image_name = f"image-{timestamp}{extension}"
            # 移除或替换文件名中的非法字符
            image_name = re.sub(r'[<>:"/\\|?*]', '_', image_name)
            image_path = os.path.join(assets_dir, image_name)
            try:
                image_data = requests.get(url, headers=headers, timeout=10).content
                with open(image_path, 'wb') as img_file:
                    img_file.write(image_data)
                return f'![image-{timestamp}](./assets/{image_name})'
            except requests.exceptions.RequestException as e:
                print(f"图片下载失败: {e}")
                return match.group(0)

        markdown_content = re.sub(r'!\[.*?\]\((.*?)\)', download_image, markdown_content)

        with open(path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
        print(f"文档 {slug} 下载成功，保存路径: {path}")
    except requests.exceptions.RequestException as e:
        print(f"请求失败: {e}")


def get_book(url, cookies=None, output_path="download"):
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    headers = {'Cookie': cookies} if cookies else {}
    try:
        docsdata = session.get(url, headers=headers, timeout=10)
        data = re.findall(r"decodeURIComponent\(\"(.+)\"\)\);", docsdata.content.decode('utf-8'))
        docsjson = json.loads(urllib.parse.unquote(data[0]))
    except requests.exceptions.RequestException as e:
        print(f"请求失败: {e}")
        return

    list = {}
    temp = {}
    md = ""
    table = str.maketrans('\/:*?"<>|\n\r', "___________")

    book_title = fetch_url_title(url, cookies)
    output_dir = os.path.join(output_path, book_title)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    def process_doc(doc):
        if doc['type'] == 'TITLE' or doc['child_uuid'] != '':
            list[doc['uuid']] = {'0': doc['title'], '1': doc['parent_uuid']}
            uuid = doc['uuid']
            temp[doc['uuid']] = ''
            while True:
                if list[uuid]['1'] != '':
                    if temp[doc['uuid']] == '':
                        temp[doc['uuid']] = doc['title'].translate(table)
                    else:
                        temp[doc['uuid']] = list[uuid]['0'].translate(table) + '/' + temp[doc['uuid']]
                    uuid = list[uuid]['1']
                else:
                    temp[doc['uuid']] = list[uuid]['0'].translate(table) + '/' + temp[doc['uuid']]
                    break
            doc_dir = os.path.join(output_dir, temp[doc['uuid']])
            if not os.path.exists(doc_dir):
                os.makedirs(doc_dir)
            if temp[doc['uuid']].endswith("/"):
                md.append("## " + temp[doc['uuid']][:-1] + "\n")
            else:
                md.append("  " * (temp[doc['uuid']].count("/") - 1) + "* " + temp[doc['uuid']][
                                                                             temp[doc['uuid']].rfind("/") + 1:] + "\n")
        if doc['url'] != '':
            if doc['parent_uuid'] != "":
                if temp[doc['parent_uuid']].endswith("/"):
                    md.append(
                        " " * temp[doc['parent_uuid']].count("/") + "* [" + doc['title'] + "](" + urllib.parse.quote(
                            temp[doc['parent_uuid']] + "/" + doc['title'].translate(table) + '.md') + ")" + "\n")
                else:
                    md.append(
                        "  " * temp[doc['parent_uuid']].count("/") + "* [" + doc['title'] + "](" + urllib.parse.quote(
                            temp[doc['parent_uuid']] + "/" + doc['title'].translate(table) + '.md') + ")" + "\n")
                save_page(str(docsjson['book']['id']), doc['url'],
                          os.path.join(output_dir, temp[doc['parent_uuid']], doc['title'].translate(table) + '.md'),
                          cookies)
            else:
                md.append(" " + "* [" + doc['title'] + "](" + urllib.parse.quote(
                    doc['title'].translate(table) + '.md') + ")" + "\n")
                save_page(str(docsjson['book']['id']), doc['url'],
                          os.path.join(output_dir, doc['title'].translate(table) + '.md'), cookies)

    md = []
    total_docs = len(docsjson['book']['toc'])
    with tqdm(total=total_docs, desc="下载进度", unit="文档") as pbar:
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(process_doc, doc) for doc in docsjson['book']['toc']]
            for future in as_completed(futures):
                future.result()
                pbar.update(1)

    with open(os.path.join(output_dir, 'SUMMARY.md'), 'w', encoding='utf-8') as f:
        f.write(''.join(md))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='从语雀下载书籍文档。')
    parser.add_argument('url', nargs='?', default="https://www.yuque.com/burpheart/phpaudit", help='书籍的 URL。')
    parser.add_argument('--cookie', default=None, help='用于认证的 Cookie。')
    parser.add_argument('--output', default="download", help='下载文件的输出目录。')

    # 交互式参数输入提示
    args = parser.parse_args()
    url = input("请输入书籍的URL(默认为https://www.yuque.com/burpheart/phpaudit): ") or args.url
    cookie = input("请输入Cookie(留空则无Cookie): ") or args.cookie
    output = input("请输入输出目录(默认为download): ") or args.output

    get_book(url, cookie, output)
