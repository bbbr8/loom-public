"""Container-side first-run check using synthetic, transient input only."""
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


def main():
    root = Path(__file__).resolve().parent
    request = json.loads((root / 'web/examples.json').read_text())[0]['request']
    key = os.environ['LOOM_API_KEY']
    url = 'http://127.0.0.1:' + os.environ.get('PORT', '10000')
    req = Request(url + '/v1/analyze', data=json.dumps(request).encode(),
                  headers={'Authorization': 'Bearer ' + key,
                           'Content-Type': 'application/json'})
    with urlopen(req, timeout=60) as response:
        result = json.load(response)
    if result.get('ok') is not True or not isinstance(result.get('result'), dict):
        raise SystemExit('Synthetic analysis did not return a successful result.')
    print('Authenticated synthetic analysis passed.')


if __name__ == '__main__':
    main()
