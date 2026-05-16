import gradio as gr
import main
import uuid
import os

def process_cookie(cookie_str):
    """处理用户提交的 Cookie 并执行全链路下载"""
    if not cookie_str.strip():
        return "⚠️ 请输入有效的 Cookie", None

    try:
        # 1. 实例化中台并注入
        auth = main.DouyinAuthenticator()
        auth._inject_raw_cookie(cookie_str.strip())
        
        # 2. 鉴权
        if not auth._check_valid():
            return "❌ 鉴权失败：Cookie 无效或已过期，请在网页端重新获取。", None
        
        # 3. 拉取列表
        engine = main.DouyinEmojiEngine(auth.session)
        engine.fetch_list()
        
        if not engine.emojis:
            return "⚠️ 该账号下未发现任何自定义表情包资源。", None
            
        # 4. 启动并发引擎并指定独立沙箱文件名
        output_zip = f"DouyinEmoji_{uuid.uuid4().hex[:8]}.zip"
        zip_path = engine.run(output_zip)
        
        if zip_path and os.path.exists(zip_path):
            size_mb = os.path.getsize(zip_path) / 1024 / 1024
            success_msg = f"🎉 提取成功！\n- 资源总数: {len(engine.emojis)} 个\n- 文件体积: {size_mb:.2f} MB"
            return success_msg, zip_path
        else:
            return "❌ 打包阶段发生错误，请查看控制台日志。", None

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"❌ 运行时捕获异常: {str(e)}", None

# 构建极简现代化 UI
with gr.Blocks(title="抖音表情包 P8 导出", theme=gr.themes.Soft()) as app:
    gr.Markdown(
        """
        # 📦 Douyin-Emoji-Toolkit (Web 版)
        > **纯协议驱动 | 高保真转换引擎 | 隐私安全**
        > 本工具通过纯协议模拟提取表情包，**不会收集、保存任何用户的 Cookie 或历史记录**。代码开源且运行在阅后即焚沙箱中。
        """
    )
    
    with gr.Row():
        cookie_input = gr.Textbox(
            label="🔑 粘贴抖音网页版 Cookie", 
            placeholder="sessionid=xxxx; passport_csrf_token=yyyy; ...",
            lines=4
        )
    
    btn = gr.Button("🚀 立即提取 (提取期间请耐心等待加载动画)", variant="primary")
    
    with gr.Row():
        status_output = gr.Textbox(label="运行结果", interactive=False)
        file_output = gr.File(label="📂 点击下载 ZIP 压缩包")
        
    btn.click(
        fn=process_cookie,
        inputs=[cookie_input],
        outputs=[status_output, file_output]
    )

if __name__ == "__main__":
    # 绑定 7860 端口适配 Hugging Face 默认规范
    app.launch(server_name="0.0.0.0", server_port=7860)