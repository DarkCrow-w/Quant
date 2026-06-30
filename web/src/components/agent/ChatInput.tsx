import { useRef, useState, type KeyboardEvent } from 'react';
import { Button, Tooltip, Upload } from 'antd';
import {
  DeleteOutlined,
  PictureOutlined,
  SendOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { useAgentStore } from '../../stores/agent';

export default function ChatInput() {
  const [text, setText] = useState('');
  const [images, setImages] = useState<string[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const store = useAgentStore();
  const imageSupported = store.runtime?.provider === 'anthropic';

  const handleSend = () => {
    const content = text.trim();
    if (!content && images.length === 0) return;
    const accepted = store.sendMessage(
      content,
      images.length ? images : undefined,
    );
    if (accepted) {
      setText('');
      setImages([]);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  };

  const handleImageUpload = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      const base64 = String(reader.result).split(',')[1];
      setImages((current) => [...current, base64]);
    };
    reader.readAsDataURL(file);
    return false;
  };

  const disabled = store.runtime?.enabled === false;
  const composerHint = disabled
    ? store.runtime?.reason || '请先配置 Agent 模型 API Key'
    : store.queuedMessage
      ? store.queuedMessage
      : store.isStreaming
        ? '正在生成研究结论，工具调用和最终答案会实时更新'
        : store.connectionState === 'connected'
          ? 'Enter 发送，Shift+Enter 换行'
          : '研究服务未连接，发送后会自动尝试连接';

  return (
    <div className="chat-composer">
      {images.length > 0 && (
        <div className="chat-image-list">
          {images.map((image, index) => (
            <div className="chat-image-preview" key={`${image.slice(0, 16)}-${index}`}>
              <img src={`data:image/png;base64,${image}`} alt="待分析图片" />
              <Button
                size="small"
                type="text"
                icon={<DeleteOutlined />}
                aria-label="移除图片"
                onClick={() =>
                  setImages((current) =>
                    current.filter((_, itemIndex) => itemIndex !== index),
                  )
                }
              />
            </div>
          ))}
        </div>
      )}

      <div className="chat-composer-inner">
        <Upload
          accept="image/*"
          showUploadList={false}
          beforeUpload={handleImageUpload}
          multiple
          disabled={!imageSupported || store.isStreaming}
        >
          <Tooltip
            title={
              imageSupported
                ? '上传图表或截图'
                : '当前 DeepSeek 模型暂使用文本研究模式'
            }
          >
            <Button
              icon={<PictureOutlined />}
              disabled={!imageSupported || store.isStreaming}
              aria-label="上传图片"
            />
          </Tooltip>
        </Upload>
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            disabled
              ? '请先配置 Agent 模型 API Key'
              : '输入研究问题，例如：分析 000001 最近 60 日趋势和风险'
          }
          disabled={disabled}
          rows={1}
        />
        {store.isStreaming ? (
          <Tooltip title="停止分析">
            <Button
              danger
              icon={<StopOutlined />}
              onClick={store.stopGeneration}
              aria-label="停止分析"
            />
          </Tooltip>
        ) : (
          <Tooltip title="发送">
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleSend}
              disabled={disabled || (!text.trim() && images.length === 0)}
              aria-label="发送"
            />
          </Tooltip>
        )}
      </div>
      <div
        className={`chat-composer-hint ${
          store.queuedMessage || store.connectionState === 'error' ? 'attention' : ''
        }`}
      >
        {composerHint}
      </div>
    </div>
  );
}
