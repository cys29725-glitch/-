from flask import Flask, render_template, request, jsonify, make_response, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import json
import os
import uuid
import logging
from datetime import datetime

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('amazing_chat')

app = Flask(__name__)
# 使用环境变量或安全的密钥
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'amazing-chat-secret-key')
# 配置Socket.IO
socketio = SocketIO(
    app, 
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=10 * 1024 * 1024
)

# 存储在线用户信息
online_users = {}
# 存储聊天室消息历史
chat_history = []

# 加载配置文件
def load_config():
    try:
        config_path = 'config.json'
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"servers": [{"name": "本地服务器", "url": "http://localhost:5000"}]}
    except Exception as e:
        logger.error(f'加载配置文件失败: {str(e)}')
        return {"servers": [{"name": "本地服务器", "url": "http://localhost:5000"}]}

config = load_config()

@app.route('/')
def index():
    try:
        return render_template('login.html', servers=config['servers'])
    except Exception as e:
        logger.error(f'渲染登录页面失败: {str(e)}')
        return make_response('服务器内部错误', 500)

@app.route('/chat')
def chat():
    try:
        username = request.args.get('username')
        server = request.args.get('server')
        
        # 验证用户名
        if not username or username.strip() == '':
            return redirect(url_for('index'))
        
        # 限制用户名长度和清理特殊字符
        username = username.strip()[:30]
        username = ''.join(c for c in username if c.isalnum() or c in ' _\u4e00-\u9fa5')
        
        return render_template('chat.html', username=username, server=server)
    except Exception as e:
        logger.error(f'渲染聊天页面失败: {str(e)}')
        return redirect(url_for('index'))

@app.route('/check_username', methods=['GET', 'POST'])
def check_username():
    try:
        # 支持GET和POST请求
        if request.method == 'POST' and request.is_json:
            data = request.get_json()
            username = data.get('username', '')
        else:
            username = request.args.get('username', '')
        
        username = username.strip()
        is_taken = username in online_users
        
        return jsonify({
            'taken': is_taken,
            'available': not is_taken,
            'message': '用户名可用' if not is_taken else '用户名已被使用'
        })
    except Exception as e:
        logger.error(f'检查用户名失败: {str(e)}')
        return jsonify({'taken': False, 'error': '服务器错误'}), 500

@socketio.on('connect')
def handle_connect():
    try:
        logger.info(f'客户端连接: {request.sid}')
        # 发送连接确认
        emit('connect_ack', {'message': '连接成功'})
    except Exception as e:
        logger.error(f'处理连接事件失败: {str(e)}')

@socketio.on('disconnect')
def handle_disconnect():
    try:
        sid = request.sid
        logger.info(f'客户端断开连接: {sid}')
        
        # 查找断开连接的用户
        username_to_remove = None
        for username, user_info in online_users.items():
            if user_info['sid'] == sid:
                username_to_remove = username
                break
        
        if username_to_remove:
            del online_users[username_to_remove]
            logger.info(f'用户离开: {username_to_remove}')
            # 通知其他用户有用户离开
            try:
                emit('user_left', {
                    'username': username_to_remove, 
                    'users': list(online_users.keys())
                }, broadcast=True, include_self=False)
            except Exception as emit_error:
                logger.error(f'发送用户离开通知失败: {str(emit_error)}')
    except Exception as e:
        logger.error(f'处理断开连接事件失败: {str(e)}')

@socketio.on('join')
def handle_join(data):
    try:
        # 验证数据格式
        if not isinstance(data, dict) or 'username' not in data:
            logger.error('加入事件数据格式错误')
            emit('error', {'message': '无效的加入请求'})
            return
        
        username = data['username']
        sid = request.sid
        
        # 验证用户名
        if not username or not isinstance(username, str) or len(username.strip()) == 0:
            logger.warning('无效的用户名')
            emit('error', {'message': '用户名不能为空'})
            return
        
        # 清理用户名
        username = username.strip()[:30]
        username = ''.join(c for c in username if c.isalnum() or c in ' _\u4e00-\u9fa5')
        
        # 检查用户名是否已存在
        if username in online_users:
            # 如果是同一个客户端重新加入，更新会话ID
            if online_users[username]['sid'] != sid:
                logger.info(f'用户重新加入: {username} (新会话ID: {sid})')
                # 通知旧会话已被挤下线
                old_sid = online_users[username]['sid']
                emit('kicked', {'message': '您的账号在其他地方登录'}, room=old_sid)
            else:
                logger.warning(f'用户重复加入: {username}')
        
        # 存储用户信息
        online_users[username] = {
            'sid': sid,
            'joined_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        logger.info(f'用户加入: {username} (会话ID: {sid})')
        
        # 加入默认房间
        join_room('chat_room')
        
        # 发送欢迎消息给新用户
        welcome_msg = {
            'type': 'system',
            'message': f'欢迎 {username} 加入聊天室！',
            'timestamp': datetime.now().strftime('%H:%M:%S')
        }
        
        # 添加系统消息到历史
        with socketio.lock:
            chat_history.append(welcome_msg)
            # 限制历史记录长度
            if len(chat_history) > 100:
                chat_history.pop(0)
        
        # 发送历史消息给新用户
        try:
            emit('history', {'messages': chat_history})
        except Exception as emit_error:
            logger.error(f'发送历史消息失败: {str(emit_error)}')
        
        # 通知所有用户有新用户加入
        try:
            emit('user_joined', {
                'username': username,
                'users': list(online_users.keys()),
                'message': welcome_msg
            }, broadcast=True)
        except Exception as emit_error:
            logger.error(f'发送用户加入通知失败: {str(emit_error)}')
            
    except Exception as e:
        logger.error(f'处理加入事件失败: {str(e)}')
        try:
            emit('error', {'message': '加入聊天室失败'})
        except:
            pass

@socketio.on('send_message')
def handle_message(data):
    try:
        # 验证数据格式
        if not isinstance(data, dict) or 'username' not in data or 'message' not in data:
            logger.error('消息数据格式错误')
            return
        
        username = data['username']
        message = data['message']
        
        # 验证用户是否在线
        if username not in online_users:
            logger.warning(f'未授权的消息发送尝试: {username}')
            try:
                emit('error', {'message': '请先加入聊天室'})
            except:
                pass
            return
        
        # 验证消息内容
        if not message or not isinstance(message, str):
            return
        
        message = message.strip()
        if not message:
            return
        
        # 限制消息长度
        if len(message) > 500:
            message = message[:500] + '...'
            
        timestamp = datetime.now().strftime('%H:%M:%S')
        
        # 处理特殊命令
        message_type = 'text'
        special_data = None
        
        # 检查是否是@命令
        if message.startswith('@电影'):
            message_type = 'movie'
            # 提取电影URL
            parts = message.split(' ', 1)
            if len(parts) > 1:
                movie_url = parts[1].strip()
                # 确保URL格式正确（添加http://前缀如果没有）
                if not movie_url.startswith(('http://', 'https://')):
                    movie_url = 'http://' + movie_url
                special_data = {'url': movie_url}
            else:
                message = '请提供电影URL，格式：@电影 <url>'
                message_type = 'text'
        elif message.startswith('@川小农'):
            message_type = 'ai_chat'
            # 提取问题
            parts = message.split(' ', 1)
            if len(parts) > 1:
                special_data = {'question': parts[1]}
                
                try:
                    # 四川农业大学AI助手回复逻辑
                    question = parts[1].strip()
                    
                    # 初始化AI回复
                    ai_response_message = ''
                    
                    # 1. 自我介绍相关
                    if any(keyword in question for keyword in ['你是谁', '你是', '介绍', '名字', '小美']):
                        ai_response_message = '你好~我是川小农，是这个Amazing聊天室专属的AI助手，小名叫小美！我是四川农业大学的AI小助手，性别是女。我的主要功能是接收用户提问，回答与四川农业大学有关的问题。有任何关于川农大的问题都可以随时问我哦！'
                    
                    # 2. 四川农业大学基本信息
                    elif any(keyword in question for keyword in ['川农大', '四川农业大学', '学校', '在哪里', '地址']):
                        if '地址' in question or '在哪里' in question:
                            ai_response_message = '四川农业大学有三个校区：雅安校区位于四川省雅安市雨城区新康路46号；成都校区位于成都市温江区惠民路211号；都江堰校区位于成都市都江堰市建设路288号。'
                        else:
                            ai_response_message = '四川农业大学是国家"双一流"建设高校，也是国家"211工程"重点建设大学。学校有雅安、成都和都江堰三个校区，学科涵盖农学、理学、工学、经济学、管理学等多个领域。'
                    
                    # 3. 专业相关问题
                    elif any(keyword in question for keyword in ['专业', '学科', '学院']):
                        ai_response_message = '四川农业大学设有农学院、动物科技学院、林学院、园艺学院、资源学院等多个学院。优势学科包括作物学、畜牧学、兽医学、林学、农林经济管理等。学校有多个国家级和省级重点学科。'
                    
                    # 4. 历史相关问题
                    elif any(keyword in question for keyword in ['历史', '成立', '创建', '多少年']):
                        ai_response_message = '四川农业大学前身是1906年创办的四川通省农业学堂，1935年成为省立四川大学农学院，1956年迁至雅安独立建校为四川农学院，1985年更名为四川农业大学。学校至今已有一百多年的办学历史。'
                    
                    # 5. 校园生活相关
                    elif any(keyword in question for keyword in ['生活', '宿舍', '食堂', '校园']):
                        ai_response_message = '四川农业大学各校区环境优美，设施完善。学生宿舍提供良好的住宿条件，配有空调、独立卫生间等设施。学校食堂菜品丰富多样，能够满足不同学生的饮食需求。校园内还有图书馆、体育馆、实验室等各类学习和生活设施。'
                    
                    # 6. 招生相关
                    elif any(keyword in question for keyword in ['招生', '分数线', '报考', '录取']):
                        ai_response_message = '四川农业大学每年面向全国招生，具体的招生计划、分数线和报考要求可以关注学校官方网站或招生办公室发布的最新信息。学校提供本科、硕士、博士等多层次的教育项目。'
                    
                    # 7. 未匹配到相关问题时的回复
                    else:
                        ai_response_message = f'感谢你的提问！关于"{parts[1]}"，我还在学习中。不过我可以回答四川农业大学的相关问题，比如学校历史、专业设置、校园环境等。你有什么关于川农大的问题想问我吗？'
                    
                    ai_response = {
                        'type': 'ai_reply',
                        'username': '川小农',
                        'message': ai_response_message,
                        'timestamp': datetime.now().strftime('%H:%M:%S')
                    }
                    
                    # 保存AI回复到历史
                    with socketio.lock:
                        chat_history.append(ai_response)
                        # 限制历史记录长度
                        if len(chat_history) > 100:
                            chat_history.pop(0)
                    
                    # 广播AI回复
                    emit('new_message', ai_response, broadcast=True, room='chat_room')
                except Exception as ai_error:
                    logger.error(f'AI回复处理失败: {str(ai_error)}')
                    # 发送错误消息
                    error_msg = {
                        'type': 'system',
                        'message': 'AI助手暂时无法响应，请稍后再试',
                        'timestamp': timestamp
                    }
                    emit('new_message', error_msg, room=request.sid)
            else:
                message = '请提供要问川小农的问题，格式：@川小农 <问题>'
                message_type = 'text'
        
        # 构建消息对象
        msg_obj = {
            'type': message_type,
            'username': username,
            'message': message,
            'timestamp': timestamp,
            'special_data': special_data
        }
        
        # 保存到历史记录
        with socketio.lock:
            chat_history.append(msg_obj)
            # 限制历史记录长度
            if len(chat_history) > 100:
                chat_history.pop(0)
        
        # 广播消息
        try:
            emit('new_message', msg_obj, broadcast=True, room='chat_room')
        except Exception as emit_error:
            logger.error(f'广播消息失败: {str(emit_error)}')
            
    except Exception as e:
        logger.error(f'处理消息事件失败: {str(e)}')
        try:
            emit('error', {'message': '发送消息失败'})
        except:
            pass

@socketio.on('leave')
def handle_leave(data):
    try:
        # 验证数据格式
        if not isinstance(data, dict) or 'username' not in data:
            logger.error('离开事件数据格式错误')
            return
        
        username = data['username']
        if username in online_users:
            del online_users[username]
            leave_room('chat_room')
            logger.info(f'用户主动离开: {username}')
            # 通知其他用户有用户离开
            try:
                emit('user_left', {
                    'username': username,
                    'users': list(online_users.keys())
                }, broadcast=True, include_self=False)
            except Exception as emit_error:
                logger.error(f'发送用户离开通知失败: {str(emit_error)}')
    except Exception as e:
        logger.error(f'处理离开事件失败: {str(e)}')

# 健康检查路由
@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'online_users': len(online_users),
        'history_count': len(chat_history),
        'timestamp': datetime.now().isoformat()
    })

# 全局错误处理
@app.errorhandler(404)
def page_not_found(e):
    return redirect(url_for('index'))

@app.errorhandler(500)
def internal_server_error(e):
    logger.error(f'内部服务器错误: {str(e)}')
    return make_response('服务器内部错误', 500)

if __name__ == '__main__':
    try:
        logger.info('启动Amazing聊天室服务器...')
        logger.info(f'服务器配置: {config}')
        # 监听所有接口，支持局域网访问
        socketio.run(app, host='0.0.0.0', port=5000, debug=True)
    except KeyboardInterrupt:
        logger.info('服务器已停止')
    except Exception as e:
        logger.critical(f'服务器启动失败: {str(e)}')
        raise