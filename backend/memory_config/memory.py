from log_config import logger
import agent_config  # 动态获取代理
from persona_config import persona
from database_config import init_db


# 定义记忆管理器
class MemoryManager:
    def __init__(self):
        logger.info('初始化记忆管理器Memory Manager...')

        # 初始化数据库
        self.db = init_db()

        # 历史对话和历史对话摘要缓存，提高运行效率
        # 加载历史对话和摘要
        self.history_cache = self.load_history(0)    # 列表
        self.abstract_cache = self.load_abstract()

        # 缓存是否有效
        self.cache_valid = True

        logger.info('记忆管理器初始化完成！')

    # 记录对话
    def record_message(self, message):
        logger.info(f'记录本轮对话...')
        # 先加入缓存
        self.history_cache.append(message)
        try:
            sql = 'insert into history_messages values(%s,%s,%s)'
            cursor = self.db.cursor()

            # 使用'|||'将助手回复的多段对话合成一个字符串，然后再存进数据库，这样读取时也可以以'|||'来拆分
            cursor.execute(sql, (message['conversation_round'], message['user'], '|||'.join(message['assistant'])))

            # 提交修改
            self.db.commit()
        except Exception as e:
            # 回滚操作
            self.db.rollback()
            logger.error(f'记录对话时出现错误:{e}')

    # 加载历史对话
    def load_history(self, k, front=True):  # k=-1加载最后一条记录，k=0加载所有记录， k>0加载k条记录， front表示应从头开始加载，还是从尾开始加载
        history_messages = []
        try:
            logger.info('加载历史对话...')
            cursor = self.db.cursor()

            if k == 0:
                logger.info('加载所有历史对话...')
                if front:
                    sql = 'select * from history_messages order by conversation_round;'
                else:
                    sql = 'select * from history_messages order by conversation_round desc;'
                cursor.execute(sql)
            elif k >= 0:
                if front:
                    logger.info(f'加载前{k}条历史对话...')
                    sql = 'select * from history_messages order by conversation_round limit %s;'
                else:
                    logger.info(f'加载后{k}条历史对话...')
                    sql = 'select * from history_messages order by conversation_round desc limit %s;'
                cursor.execute(sql, [k])
            else:
                if front:
                    logger.info('加载最后一条对话...')
                    sql = 'select * from history_messages order by conversation_round desc limit 1;'
                else:
                    logger.info('加载第一条历史对话...')
                    sql = 'select * from history_messages order by conversation_round limit 1;'
                cursor.execute(sql)

            all_data = cursor.fetchall()
            for data in all_data:
                history_messages.append({'conversation_round': data[0], 'user': data[1], 'assistant': data[2].split('|||')})

        except Exception as e:
            logger.info(f'加载历史对话失败:{e}，返回空列表')

        # 返回历史对话（列表形式）
        """
        [{'conversation_round':...,'user':'...', 'assistant':'...'}, ...]
        """
        return history_messages

    # 加载历史对话摘要
    def load_abstract(self):
        try:
            logger.info('加载历史对话摘要...')
            cursor = self.db.cursor()
            sql = "select * from abstract_messages;"
            cursor.execute(sql)
            data = cursor.fetchone()
            if data == None:
                text = ''
            else:
                text = data[0]

        except Exception as e:
            logger.info(f'加载历史对话摘要失败:{e}，返回空历史摘要')
            text = ''

        return text

    # 压缩历史对话，生成摘要
    def compress_history(self):
        try:
            logger.info('压缩历史对话...')
            cursor = self.db.cursor()
            # 先获取总历史对话数
            sql = "select count(*) from history_messages;"
            cursor.execute(sql)
            data = cursor.fetchone()

            # 压缩一半的对话记录
            length = data[0]
            k = int(length / 2)
            half_history = self.load_history(k)

            # 加载历史对话摘要，一起压缩
            abstract_history = self.load_abstract()

            analysis_prompt = f"""请你对用户（对应user）和{persona.BOT_NAME}（对应assistant）的历史对话及更早的历史对话摘要进行缩减，保留重点，返回精确的重点摘要。
历史对话：{half_history}。
更早的历史对话摘要：{abstract_history}。"""

            response = agent_config.text_agent.invoke(
                {"messages": [{"role": "user", "content": analysis_prompt}]}
            )

            # 提取出回复
            messages = response.get("messages", [])
            if not messages:
                raise ValueError("模型返回结果中没有 messages")
            compressed_history = messages[-1].content

            # 写入新的摘要
            sql = 'update abstract_messages set abstract=%s;'
            cursor.execute(sql, [compressed_history])

            # 删除压缩的历史对话，保留较新的历史对话
            sql = 'delete from history_messages order by conversation_round limit %s;'
            cursor.execute(sql, [k])

            # 提交修改
            self.db.commit()

            # 缓存置为无效
            self.cache_valid = False

        except Exception as e:
            # 回滚操作
            self.db.rollback()
            logger.info(f'压缩历史对话失败:{e}')


memorier = MemoryManager()
