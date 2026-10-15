#!/usr/bin/env python3
import sys
import requests
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding='utf-8')

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1'
}

url = "https://arena.ai/"
print(f"访问 {url} ...\n")

try:
    r = requests.get(url, headers=headers, timeout=15)
    print(f"状态码: {r.status_code}")
    print(f"Content-Type: {r.headers.get('Content-Type')}")

    # 查找 leaderboard 相关链接
    soup = BeautifulSoup(r.text, 'html.parser')
    links = soup.find_all('a', href=True)

    leaderboard_links = [a['href'] for a in links if 'leader' in a['href'].lower() or 'ranking' in a['href'].lower()]

    if leaderboard_links:
        print(f"\n找到 {len(leaderboard_links)} 个排行榜相关链接:")
        for link in set(leaderboard_links)[:10]:
            print(f"  - {link}")
    else:
        print("\n未找到排行榜链接，显示所有链接前 20 个:")
        for link in list(set([a['href'] for a in links]))[:20]:
            print(f"  - {link}")

except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
