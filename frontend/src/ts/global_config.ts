export const modelConfig: {model_valid: boolean, model: {model_name: string, model_merchant: string}} = {
    model_valid: false,//模型是否有效
    model:{//模型配置
        model_merchant: '',
        model_name:''
    }
}


export const personaConfig: {persona_valid: boolean, persona: {BOT_AVATAR: string, BOT_NAME: string, BOT_BIRTHDAY: string, BOT_BIRTHPLACE: string, USER_AVATAR: string}} = {
    persona_valid: false,//人格是否有效
    persona: {
        BOT_AVATAR: '',
        BOT_NAME: '',
        BOT_BIRTHDAY: '',
        BOT_BIRTHPLACE: '',
        USER_AVATAR: ''
    }
}


//历史对话表中每个元素的接口
export interface ConversationItem {
  conversation_round: number;
  user: string;
  assistant: string[];  // 多条助手回复
}