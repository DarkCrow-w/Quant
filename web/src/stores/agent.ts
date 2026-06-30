import { create } from 'zustand';
import type {
  AgentRuntimeStatus,
  AgentMode,
  AgentStatus,
  AgentToolCall,
  ChatMessage,
  ServerFrame,
  SessionSummary,
} from '../types';
import {
  createAgentWebSocket,
  deleteSession,
  fetchAgentRuntime,
  fetchSessions,
} from '../api/agent';

type ConnectionState = 'offline' | 'connecting' | 'connected' | 'error';
type CachedConversation = {
  sessionId: string | null;
  messages: ChatMessage[];
};

const AGENT_MODES: AgentMode[] = [
  'auto',
  'quant',
  'market',
  'screening',
  'backtest',
];
const CONVERSATION_STORAGE_KEY = 'quantlab.agent.conversations.v1';
const SELECTED_MODE_STORAGE_KEY = 'quantlab.agent.selected-mode.v1';
const MAX_CACHED_MESSAGES_PER_MODE = 30;
const MAX_CACHED_CONTENT_CHARS = 12_000;
const MAX_CACHED_TOOL_PAYLOAD_CHARS = 12_000;
const FALLBACK_CACHED_MESSAGES_PER_MODE = 10;

const emptyConversation = (): CachedConversation => ({
  sessionId: null,
  messages: [],
});

const loadConversationCache = (): Record<AgentMode, CachedConversation> => {
  const initial = Object.fromEntries(
    AGENT_MODES.map((mode) => [mode, emptyConversation()]),
  ) as Record<AgentMode, CachedConversation>;
  if (typeof window === 'undefined') return initial;
  try {
    const stored = JSON.parse(
      window.localStorage.getItem(CONVERSATION_STORAGE_KEY) ?? '{}',
    ) as Partial<Record<AgentMode, CachedConversation>>;
    for (const mode of AGENT_MODES) {
      const conversation = stored[mode];
      if (conversation && Array.isArray(conversation.messages)) {
        initial[mode] = {
          sessionId: conversation.sessionId ?? null,
          messages: compactMessagesForCache(conversation.messages),
        };
      }
    }
  } catch {
    window.localStorage.removeItem(CONVERSATION_STORAGE_KEY);
  }
  return initial;
};

const loadSelectedMode = (): AgentMode => {
  if (typeof window === 'undefined') return 'auto';
  const stored = window.localStorage.getItem(SELECTED_MODE_STORAGE_KEY);
  return AGENT_MODES.includes(stored as AgentMode)
    ? (stored as AgentMode)
    : 'auto';
};

let conversationCache = loadConversationCache();
const initialSelectedMode = loadSelectedMode();

const persistConversation = (
  mode: AgentMode,
  sessionId: string | null,
  messages: ChatMessage[],
) => {
  const compactMessages = compactMessagesForCache(messages);
  conversationCache = {
    ...conversationCache,
    [mode]: { sessionId, messages: compactMessages },
  };
  if (typeof window !== 'undefined') {
    try {
      window.localStorage.setItem(
        CONVERSATION_STORAGE_KEY,
        JSON.stringify(conversationCache),
      );
    } catch {
      const fallback = compactCacheForStorageFailure(conversationCache);
      conversationCache = fallback;
      try {
        window.localStorage.setItem(
          CONVERSATION_STORAGE_KEY,
          JSON.stringify(fallback),
        );
      } catch {
        window.localStorage.removeItem(CONVERSATION_STORAGE_KEY);
      }
    }
  }
};

function truncateText(value: string, maxChars: number) {
  if (value.length <= maxChars) return value;
  return `${value.slice(0, maxChars)}\n\n（本地会话缓存已截断更早的长内容，完整结果以当前页面为准。）`;
}

function compactRecordForCache(
  value: Record<string, unknown>,
  maxChars: number,
): Record<string, unknown> {
  try {
    const serialized = JSON.stringify(value);
    if (serialized.length <= maxChars) return value;
    return {
      cached_summary: '工具结果较大，已在本地会话缓存中压缩。',
      cached_preview: serialized.slice(0, maxChars),
    };
  } catch {
    return {
      cached_summary: '工具结果无法序列化，已在本地会话缓存中省略。',
    };
  }
}

function compactToolCallForCache(toolCall: AgentToolCall): AgentToolCall {
  return {
    ...toolCall,
    input: compactRecordForCache(toolCall.input, 4_000),
    result: toolCall.result
      ? compactRecordForCache(toolCall.result, MAX_CACHED_TOOL_PAYLOAD_CHARS)
      : undefined,
  };
}

function compactMessageForCache(message: ChatMessage): ChatMessage {
  return {
    ...message,
    content: truncateText(message.content, MAX_CACHED_CONTENT_CHARS),
    images: undefined,
    toolCalls: message.toolCalls?.map(compactToolCallForCache),
  };
}

function compactMessagesForCache(messages: ChatMessage[]) {
  return messages
    .slice(-MAX_CACHED_MESSAGES_PER_MODE)
    .map(compactMessageForCache);
}

function compactCacheForStorageFailure(
  cache: Record<AgentMode, CachedConversation>,
): Record<AgentMode, CachedConversation> {
  return Object.fromEntries(
    AGENT_MODES.map((mode) => {
      const conversation = cache[mode] ?? emptyConversation();
      const messages = conversation.messages
        .slice(-FALLBACK_CACHED_MESSAGES_PER_MODE)
        .map((message) => ({
          ...message,
          content: truncateText(message.content, 4_000),
          images: undefined,
          toolCalls: undefined,
        }));
      return [mode, { sessionId: conversation.sessionId, messages }];
    }),
  ) as Record<AgentMode, CachedConversation>;
}

interface AgentStore {
  selectedMode: AgentMode;
  sessionId: string | null;
  sessions: SessionSummary[];
  messages: ChatMessage[];
  isStreaming: boolean;
  streamingContent: string;
  activeAgents: AgentStatus[];
  pendingToolCalls: AgentToolCall[];
  currentRequest: string | null;
  ws: WebSocket | null;
  connected: boolean;
  connectionState: ConnectionState;
  connectionError: string | null;
  queuedMessage: string | null;
  runtime: AgentRuntimeStatus | null;
  setAgentMode: (mode: AgentMode) => void;
  connect: () => void;
  disconnect: () => void;
  reconnect: () => void;
  loadRuntime: () => Promise<AgentRuntimeStatus | null>;
  sendMessage: (content: string, images?: string[]) => boolean;
  retryLastMessage: () => boolean;
  stopGeneration: () => void;
  clearSession: () => void;
  loadSessions: () => Promise<void>;
  removeSession: (id: string) => Promise<void>;
}

let counter = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let connectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectAttempts = 0;
let allowReconnect = true;
let pendingPayload: Record<string, unknown> | null = null;
let stopRequested = false;
let intentionalCloseRequested = false;

const nextId = (prefix = 'msg') => `${prefix}_${Date.now()}_${++counter}`;

const assistantMessage = (content: string): ChatMessage => ({
  id: nextId(),
  role: 'assistant',
  content,
  timestamp: Date.now(),
});

const processOpeningPattern =
  /^\s*(?:好的[，。,\s]*)?(?:我(?:来|先|会|将)|现在|首先|接下来|下面|让我们)/;
const presentationOpeningPattern =
  /^\s*好的[，。,\s]*(?:以下是|这是|为你|给你|根据|)/;
const summaryOpeningPattern =
  /^\s*(?:扫描完成|回测完成|分析完成|筛选完成|共扫描|已完成)/;
const identifyOpeningPattern =
  /^\s*(?:\d{6}|[\u4e00-\u9fffA-Za-z]{2,20})\s*(?:是|为).{0,80}?(?:现在|获取|查询|分析|读取)/;
const publicAnswerStartPattern =
  /(#{1,3}\s*(?:结论|核心|关键|风险|筛选|扫描|回测|技术|行情)|(?:^|\n)\s*(?:结论|核心指标|关键证据|风险提示|筛选结论|扫描结果|回测结果)[:：]?)/;
const markdownHeadingPattern = /#{1,3}\s+\S/;
const markdownHeadingLinePattern = /^(#{1,6})(\s+)(.*)$/;

function isHeadingDecoration(char: string) {
  const codePoint = char.codePointAt(0) ?? 0;
  return (
    (codePoint >= 0x1f300 && codePoint <= 0x1faff) ||
    (codePoint >= 0x2600 && codePoint <= 0x27bf) ||
    codePoint === 0xfe0f ||
    codePoint === 0x200d
  );
}

function stripHeadingDecoration(content: string) {
  return content
    .split('\n')
    .map((line) => {
      const match = line.match(markdownHeadingLinePattern);
      if (!match) return /^#{1,6}\s*$/.test(line.trim()) ? null : line;
      const [, prefix, spacing, rawTitle] = match;
      let title = rawTitle.trimStart();
      let first = Array.from(title)[0];
      while (first && isHeadingDecoration(first)) {
        title = title.slice(first.length).trimStart();
        first = Array.from(title)[0];
      }
      if (!title) return null;
      return `${prefix}${spacing}${title}`;
    })
    .filter((line): line is string => line !== null)
    .join('\n');
}

function stripProcessPreamble(content: string) {
  const heading = content.match(markdownHeadingPattern);
  if (heading?.index && heading.index > 0 && heading.index <= 800) {
    const prefix = content.slice(0, heading.index).trim();
    if (
      prefix.startsWith('好的') ||
      /(您好|查询|获取|读取|分析|指标|数据|现在|我先|让我|转交|工具|Agent|扫描完成|回测完成|分析完成|筛选完成|命中|候选)/.test(prefix)
    ) {
      return stripHeadingDecoration(content.slice(heading.index).trimStart());
    }
  }
  if (
    !processOpeningPattern.test(content) &&
    !presentationOpeningPattern.test(content) &&
    !summaryOpeningPattern.test(content) &&
    !identifyOpeningPattern.test(content)
  ) return stripHeadingDecoration(content);
  const match = content.match(publicAnswerStartPattern);
  if (match?.index && match.index > 0) {
    return stripHeadingDecoration(content.slice(match.index).trimStart());
  }
  if (heading?.index && heading.index > 0) {
    return stripHeadingDecoration(content.slice(heading.index).trimStart());
  }
  return stripHeadingDecoration(content);
}

function friendlyAgentError(error?: string) {
  const message = error || 'Agent 执行失败';
  if (/API|api key|key|token|401|403|未配置|configured/i.test(message)) {
    return [
      '## 分析未完成',
      '',
      'Agent 模型暂不可用，请检查模型 API Key 配置后重试。',
      '',
      '- 建议：确认 DeepSeek 配置后点击“重试上一问”。',
    ].join('\n');
  }
  if (/timeout|timed out|超时/i.test(message)) {
    return [
      '## 分析未完成',
      '',
      '本次分析超时了，可能是请求范围过大，或数据/模型响应较慢。',
      '',
      '- 建议：缩小股票范围、缩短回测区间，或点击“重试上一问”。',
    ].join('\n');
  }
  if (/connection|network|connect|网络|连接/i.test(message)) {
    return [
      '## 分析未完成',
      '',
      '研究服务连接异常，本次结果没有完整返回。',
      '',
      '- 建议：先重新连接研究服务，再点击“重试上一问”。',
    ].join('\n');
  }
  if (/not found|unsupported|不支持|unknown/i.test(message)) {
    return [
      '## 分析未完成',
      '',
      `这次请求暂时无法处理：${message}`,
      '',
      '- 建议：换一个更具体的问题，或改用行情分析/选股/回测专属模式。',
    ].join('\n');
  }
  return [
    '## 分析未完成',
    '',
    `分析没有完成：${message}`,
    '',
    '- 建议：保留当前问题，稍后点击“重试上一问”。',
  ].join('\n');
}

function interruptionMessage(kind: 'stopped' | 'disconnected') {
  if (kind === 'stopped') {
    return [
      '## 分析未完成',
      '',
      '你已停止本次分析，当前没有继续消耗模型或数据工具请求。',
      '',
      '- 建议：可以修改问题后重新发送，或点击“重试上一问”从头执行。',
    ].join('\n');
  }
  return [
    '## 分析未完成',
    '',
    '研究服务连接中断，本次结果没有完整返回。',
    '',
    '- 建议：先点击“重新连接”，再点击“重试上一问”。',
  ].join('\n');
}

function interruptedPartialMessage(
  content: string,
  kind: 'stopped' | 'disconnected',
) {
  const cleaned = content.trim();
  if (!cleaned) return interruptionMessage(kind);
  const title = kind === 'stopped' ? '分析已停止' : '分析已中断';
  const body =
    kind === 'stopped'
      ? '上方是停止前已经生成的部分结果，可能尚未覆盖完整风险、参数或结论。'
      : '上方是连接中断前已经生成的部分结果，可能尚未覆盖完整风险、参数或结论。';
  const action =
    kind === 'stopped'
      ? '可以修改问题后重新发送，或点击“重试上一问”从头执行。'
      : '请先点击“重新连接”，再点击“重试上一问”从头执行。';
  return [
    cleaned,
    '',
    '---',
    '',
    `## ${title}`,
    '',
    body,
    '',
    `- 建议：${action}`,
  ].join('\n');
}

const AGENT_DISPLAY_NAMES: Record<string, string> = {
  backtest_agent: '回测专家',
  screening_agent: '选股专家',
  market_agent: '行情专家',
  quant_agent: 'Quant Agent',
  supervisor: '研究主管',
};

export const useAgentStore = create<AgentStore>((set, get) => ({
  selectedMode: initialSelectedMode,
  sessionId: conversationCache[initialSelectedMode].sessionId,
  sessions: [],
  messages: conversationCache[initialSelectedMode].messages,
  isStreaming: false,
  streamingContent: '',
  activeAgents: [],
  pendingToolCalls: [],
  currentRequest: null,
  ws: null,
  connected: false,
  connectionState: 'offline',
  connectionError: null,
  queuedMessage: null,
  runtime: null,

  setAgentMode: (mode) => {
    if (mode === get().selectedMode || get().isStreaming) return;
    pendingPayload = null;
    const current = get();
    persistConversation(
      current.selectedMode,
      current.sessionId,
      current.messages,
    );
    const next = conversationCache[mode] ?? emptyConversation();
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(SELECTED_MODE_STORAGE_KEY, mode);
    }
    set({
      selectedMode: mode,
      sessionId: next.sessionId,
      messages: next.messages,
      streamingContent: '',
      activeAgents: [],
      pendingToolCalls: [],
      currentRequest: null,
      connectionError: null,
      queuedMessage: null,
    });
  },

  loadRuntime: async () => {
    try {
      const runtime = await fetchAgentRuntime();
      set({ runtime, connectionError: runtime.reason ?? null });
      return runtime;
    } catch (error: unknown) {
      set({
        runtime: null,
        connectionState: 'error',
        queuedMessage: null,
        connectionError:
          error instanceof Error ? error.message : '无法读取 Agent 运行状态',
      });
      return null;
    }
  },

  connect: () => {
    if (get().runtime?.enabled === false) {
      allowReconnect = false;
      set({ connected: false, connectionState: 'offline' });
      return;
    }
    const current = get().ws;
    if (
      current &&
      (current.readyState === WebSocket.OPEN ||
        current.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    if (connectTimer) return;

    allowReconnect = true;
    set({ connectionState: 'connecting', connectionError: null });
    connectTimer = setTimeout(() => {
      connectTimer = null;
      if (!allowReconnect) return;
      const active = get().ws;
      if (
        active &&
        (active.readyState === WebSocket.OPEN ||
          active.readyState === WebSocket.CONNECTING)
      ) {
        return;
      }
      const socket = createAgentWebSocket();
      set({ ws: socket });

      socket.onopen = () => {
        if (get().ws !== socket) return;
        reconnectAttempts = 0;
        set({
          connected: true,
          connectionState: 'connected',
          connectionError: null,
          queuedMessage: null,
        });
        if (pendingPayload) {
          socket.send(JSON.stringify(pendingPayload));
          pendingPayload = null;
        }
      };

      socket.onmessage = (event) => {
        try {
          handleFrame(JSON.parse(event.data) as ServerFrame, set, get);
          const state = get();
          persistConversation(
            state.selectedMode,
            state.sessionId,
            state.messages,
          );
        } catch {
          set({ connectionError: '收到无法解析的 Agent 消息' });
        }
      };

      socket.onerror = () => {
        if (get().ws !== socket) return;
        set({
          connectionState: 'error',
          queuedMessage: pendingPayload ? '连接失败，正在准备重试' : null,
          connectionError: '研究服务连接失败',
        });
      };

      socket.onclose = () => {
        if (get().ws !== socket) return;
        set({ ws: null, connected: false, connectionState: 'offline' });
        if (get().isStreaming && !stopRequested && !intentionalCloseRequested) {
          set((state) => ({
            isStreaming: false,
            streamingContent: '',
            activeAgents: [],
            pendingToolCalls: [],
            currentRequest: null,
            queuedMessage: null,
            messages: [
              ...state.messages,
              {
                id: nextId(),
                role: 'assistant',
                content: interruptedPartialMessage(
                  state.streamingContent,
                  'disconnected',
                ),
                toolCalls:
                  state.pendingToolCalls.length > 0
                    ? [...state.pendingToolCalls]
                    : undefined,
                timestamp: Date.now(),
              },
            ],
          }));
          const state = get();
          persistConversation(
            state.selectedMode,
            state.sessionId,
            state.messages,
          );
        }
        stopRequested = false;
        intentionalCloseRequested = false;
        if (allowReconnect) {
          reconnectAttempts += 1;
          const delay = Math.min(15_000, 750 * 2 ** (reconnectAttempts - 1));
          reconnectTimer = setTimeout(() => get().connect(), delay);
        }
      };
    }, 50);
  },

  disconnect: () => {
    allowReconnect = false;
    pendingPayload = null;
    intentionalCloseRequested = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (connectTimer) clearTimeout(connectTimer);
    connectTimer = null;
    get().ws?.close();
    set({
      ws: null,
      connected: false,
      connectionState: 'offline',
      isStreaming: false,
      streamingContent: '',
      activeAgents: [],
      pendingToolCalls: [],
      currentRequest: null,
      queuedMessage: null,
    });
  },

  reconnect: () => {
    if (get().runtime?.enabled === false) {
      allowReconnect = false;
      pendingPayload = null;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (connectTimer) clearTimeout(connectTimer);
      connectTimer = null;
      intentionalCloseRequested = true;
      get().ws?.close();
      set({ ws: null, connected: false, connectionState: 'offline' });
      return;
    }
    allowReconnect = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (connectTimer) clearTimeout(connectTimer);
    connectTimer = null;
    intentionalCloseRequested = true;
    get().ws?.close();
    set({ ws: null, connected: false });
    setTimeout(() => get().connect(), 50);
  },

  sendMessage: (content, images) => {
    const text = content.trim();
    if ((!text && !images?.length) || get().isStreaming) return false;
    if (get().runtime?.enabled === false) {
      set({ connectionError: get().runtime?.reason || '请先配置 Agent 模型 API Key' });
      return false;
    }

    const userMessage: ChatMessage = {
      id: nextId(),
      role: 'user',
      content: text,
      images,
      timestamp: Date.now(),
    };
    const payload: Record<string, unknown> = {
      type: 'message',
      session_id: get().sessionId,
      content: text,
      agent_mode: get().selectedMode,
    };
    if (images?.length) {
      payload.images = images.map((data) => ({
        data,
        media_type: 'image/png',
      }));
    }

    const socket = get().ws;
    const socketReady = socket?.readyState === WebSocket.OPEN;

    set((state) => ({
      messages: [...state.messages, userMessage],
      isStreaming: true,
      streamingContent: '',
      activeAgents: [],
      pendingToolCalls: [],
      currentRequest: text || (images?.length ? '图片分析请求' : null),
      connectionError: null,
      queuedMessage:
        socketReady
          ? null
          : '正在连接研究服务，连接后会自动发送',
    }));
    const state = get();
    persistConversation(
      state.selectedMode,
      state.sessionId,
      state.messages,
    );

    if (socketReady) {
      socket.send(JSON.stringify(payload));
    } else {
      pendingPayload = payload;
      set({ queuedMessage: '正在连接研究服务，连接后会自动发送' });
      get().connect();
    }
    return true;
  },

  retryLastMessage: () => {
    const lastUserMessage = [...get().messages]
      .reverse()
      .find((message) => message.role === 'user');
    if (!lastUserMessage) return false;
    return get().sendMessage(lastUserMessage.content, lastUserMessage.images);
  },

  stopGeneration: () => {
    if (!get().isStreaming) return;
    stopRequested = true;
    pendingPayload = null;
    get().ws?.close(1000, 'cancelled');
    set((state) => ({
      isStreaming: false,
      streamingContent: '',
      activeAgents: [],
      pendingToolCalls: [],
      currentRequest: null,
      queuedMessage: null,
      messages: [
        ...state.messages,
        {
          id: nextId(),
          role: 'assistant',
          content: interruptedPartialMessage(state.streamingContent, 'stopped'),
          toolCalls:
            state.pendingToolCalls.length > 0
              ? [...state.pendingToolCalls]
              : undefined,
          timestamp: Date.now(),
        },
      ],
    }));
    const state = get();
    persistConversation(
      state.selectedMode,
      state.sessionId,
      state.messages,
    );
  },

  clearSession: () => {
    pendingPayload = null;
    const mode = get().selectedMode;
    persistConversation(mode, null, []);
    set({
      sessionId: null,
      messages: [],
      isStreaming: false,
      streamingContent: '',
      activeAgents: [],
      pendingToolCalls: [],
      currentRequest: null,
      queuedMessage: null,
    });
  },

  loadSessions: async () => {
    try {
      set({ sessions: await fetchSessions() });
    } catch {
      set({ sessions: [] });
    }
  },

  removeSession: async (id) => {
    await deleteSession(id);
    set((state) => ({
      sessions: state.sessions.filter((session) => session.session_id !== id),
    }));
    if (get().sessionId === id) get().clearSession();
  },
}));

function handleFrame(
  frame: ServerFrame,
  set: (
    value:
      | Partial<AgentStore>
      | ((state: AgentStore) => Partial<AgentStore>),
  ) => void,
  get: () => AgentStore,
) {
  switch (frame.type) {
    case 'session_init':
      set({ sessionId: frame.session_id ?? null });
      break;
    case 'text_delta':
      if (frame.content) {
        set((state) => ({
          streamingContent: stripProcessPreamble(
            state.streamingContent + frame.content,
          ),
        }));
      }
      break;
    case 'agent_dispatch':
      if (frame.agent) {
        set((state) => ({
          activeAgents: [
            ...state.activeAgents.filter((agent) => agent.name !== frame.agent),
            {
              name: frame.agent!,
              displayName: AGENT_DISPLAY_NAMES[frame.agent!] ?? frame.agent!,
              status: 'working',
              task: frame.content,
            },
          ],
        }));
      }
      break;
    case 'agent_complete':
      if (frame.agent) {
        set((state) => ({
          activeAgents: state.activeAgents.map((agent) =>
            agent.name === frame.agent
              ? { ...agent, status: 'done' as const }
              : agent,
          ),
        }));
      }
      break;
    case 'tool_call':
      if (frame.tool) {
        set((state) => {
          const id = frame.tool_call_id || nextId('tool');
          const existingIndex = frame.tool_call_id
            ? state.pendingToolCalls.findIndex((call) => call.id === frame.tool_call_id)
            : -1;
          if (existingIndex >= 0) {
            const nextCalls = [...state.pendingToolCalls];
            nextCalls[existingIndex] = {
              ...nextCalls[existingIndex],
              tool: frame.tool!,
              agent: frame.agent ?? nextCalls[existingIndex].agent,
              input: frame.input ?? nextCalls[existingIndex].input,
            };
            return { pendingToolCalls: nextCalls };
          }
          return {
            pendingToolCalls: [
              ...state.pendingToolCalls,
              {
                id,
                tool: frame.tool!,
                agent: frame.agent,
                input: frame.input ?? {},
                status: 'running',
              },
            ],
          };
        });
      }
      break;
    case 'tool_result':
      if (frame.tool) {
        const calls = [...get().pendingToolCalls];
        const index = calls.findIndex(
          (call) =>
            (frame.tool_call_id && call.id === frame.tool_call_id) ||
            (!frame.tool_call_id &&
              call.tool === frame.tool &&
              call.status === 'running'),
        );
        if (index >= 0) {
          const hasError =
            Boolean(frame.data?.error) ||
            Boolean(frame.data?.detail) ||
            frame.data?.status === 'error';
          calls[index] = {
            ...calls[index],
            result: frame.data,
            status: hasError ? 'error' : 'done',
          };
          set({ pendingToolCalls: calls });
        }
      }
      break;
    case 'error':
      pendingPayload = null;
      set((state) => ({
        isStreaming: false,
        streamingContent: '',
        activeAgents: [],
        currentRequest: null,
        queuedMessage: null,
        messages: [
          ...state.messages,
          assistantMessage(friendlyAgentError(frame.error)),
        ],
      }));
      break;
    case 'done': {
      const { streamingContent, pendingToolCalls } = get();
      set((state) => ({
        isStreaming: false,
        streamingContent: '',
        pendingToolCalls: [],
        activeAgents: [],
        currentRequest: null,
        queuedMessage: null,
        messages: [
          ...state.messages,
          {
            id: nextId(),
            role: 'assistant',
            content: streamingContent || '分析已完成。',
            toolCalls:
              pendingToolCalls.length > 0 ? [...pendingToolCalls] : undefined,
            timestamp: Date.now(),
          },
        ],
      }));
      break;
    }
  }
}
