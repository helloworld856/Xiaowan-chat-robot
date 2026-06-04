import uvicorn
from config import configer


def main():
    uvicorn.run('app.main:app', host=configer.listen_host, port=configer.listen_port, reload=True)


if __name__ == '__main__':
    main()