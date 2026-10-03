export interface ModelInfo {
    model_name: string;
    model_merchant: string;
}

export interface ModelConfig {
    model_valid: boolean;
    model: ModelInfo;
}

export interface PersonaInfo {
    BOT_AVATAR: string;
    BOT_NAME: string;
    BOT_BIRTHDAY: string;
    BOT_BIRTHPLACE: string;
    USER_AVATAR: string;
}

export interface PersonaConfig {
    persona_valid: boolean;
    persona: PersonaInfo;
}

export interface ChatResponse {
    response: string[];
    conversation_round: number | null;
    saved: boolean;
}

export const modelConfig: ModelConfig = {
    model_valid: false,//模型是否有效
    model:{//模型配置
        model_merchant: '',
        model_name:''
    }
}


export const personaConfig: PersonaConfig = {
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
