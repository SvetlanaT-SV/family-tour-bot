import requests

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

session = requests.Session()
session.headers.update(headers)

r = session.get('https://www.pegast.ru/hot/')
print('Статус:', r.status_code)
print('Размер:', len(r.text))

if r.status_code == 200:
    with open('pegas_hot.html', 'w', encoding='utf-8') as f:
        f.write(r.text)
    print('Сохранено в pegas_hot.html')
else:
    print('Ошибка:', r.text[:200])
