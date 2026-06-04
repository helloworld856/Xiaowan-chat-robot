from state_config import AIChatState
from graph_config import create_graph
from state_config import State
from dotenv import load_dotenv
from persona_config import persona
from random import choice


def main():
    # 加载.env到当前进行的环境变量里
    load_dotenv()

    # 定义初始状态
    state: AIChatState = State

    # 构建图
    graph = create_graph()

    print("=" * 50)
    print("输入 'quit' 退出")
    print('-' * 50)
    print(choice(persona.WELCOME_MESSAGES))

    while True:
        try:
            user_input = input(f"\n[{state['message']['conversation_round']}]You: ").strip()

            if user_input == 'quit':
                print(f'{persona.BOT_NAME}:', choice(persona.GOODBYE_MESSAGES))
                break

            state['user_input'] = user_input

            print("⏳对方正在输入中...", end='')

            # 执行工作流
            state = graph.invoke(state)

            # 打印回复
            print(f'\r{persona.BOT_NAME}:')
            for reply in state['ai_response']:
                print(' ', reply)

        except Exception as e:
            pass


if __name__ == '__main__':
    main()

