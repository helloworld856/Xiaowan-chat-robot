export interface ModelInfo {
    model_name: string;
    model_merchant: string;
    base_url?: string;
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

// 回复列表对应逐条显示的气泡，轮次与保存状态用于历史对账。
export interface ChatResponse {
    response: string[];
    conversation_round: number | null;
    saved: boolean;
}

export interface InputLimits {
    password_min_length: number;
    password_max_length: number;
    max_nickname_chars: number;
    max_message_chars: number;
}

// 首次会话检查会应用服务端配置，兼容未返回限制的旧后端。
export const inputLimits: InputLimits = {
    password_min_length: 6,
    password_max_length: 15,
    max_nickname_chars: 32,
    max_message_chars: 8000
};

// 只缓存界面所需的模型信息，不包含个人 API Key。
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
