import uvicorn
from dotenv import load_dotenv

from config import configer

load_dotenv()


def main():
    uvicorn.run('app.main:app', host=configer.listen_host, port=configer.listen_port, workers=1)


if __name__ == '__main__':
    main()
