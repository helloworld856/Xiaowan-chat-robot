import uvicorn
from config import configer
from dotenv import load_dotenv

load_dotenv()


def main():
    uvicorn.run('app.main:app', host=configer.listen_host, port=configer.listen_port, reload=False)  # 热更新，为true时有内容修改自动重启服务器


if __name__ == '__main__':
    main()