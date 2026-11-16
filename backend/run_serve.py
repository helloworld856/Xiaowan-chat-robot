from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from config import configer

def main():
    load_dotenv(Path(__file__).resolve().with_name('.env'))
    uvicorn.run('app.main:app', host=configer.listen_host, port=configer.listen_port, workers=1)


if __name__ == '__main__':
    main()
