from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, send, emit, join_room, leave_room
import os
from datetime import datetime
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = 'секретный-ключ-для-чата-12345'
socketio = SocketIO(app, cors_allowed_origins="*")

# Хранилище данных
messages = {}  # {room_id: [messages]}
users = {}     # {username: {'sid': sid, 'rooms': []}}
private_chats = {}  # {room_id: {'type': 'private', 'users': [user1, user2]}}
groups = {}         # {room_id: {'name': 'Group Name', 'creator': username, 'members': [users], 'invite_code': code}}

# Вспомогательные функции
def get_or_create_private_room(user1, user2):
    """Создает или возвращает комнату для личного чата"""
    sorted_users = sorted([user1, user2])
    room_id = f"private_{sorted_users[0]}_{sorted_users[1]}"
    
    if room_id not in private_chats:
        private_chats[room_id] = {
            'type': 'private',
            'users': [user1, user2],
            'created_at': datetime.now()
        }
        messages[room_id] = []
    
    return room_id

def create_group(room_name, creator, members):
    """Создает новую группу"""
    room_id = f"group_{uuid.uuid4().hex[:8]}"
    invite_code = uuid.uuid4().hex[:6].upper()
    
    groups[room_id] = {
        'name': room_name,
        'creator': creator,
        'members': members,
        'invite_code': invite_code,
        'created_at': datetime.now()
    }
    messages[room_id] = []
    
    return room_id, invite_code

@app.route('/')
def index():
    return render_template('login.html')

@app.route('/chat')
def chat():
    if 'username' not in session:
        return redirect(url_for('index'))
    
    # Получаем все комнаты пользователя
    user_rooms = []
    username = session['username']
    
    # Приватные чаты
    for room_id, room in private_chats.items():
        if username in room['users']:
            other_user = room['users'][0] if room['users'][1] == username else room['users'][0]
            user_rooms.append({
                'id': room_id,
                'name': other_user,
                'type': 'private',
                'avatar': '👤'
            })
    
    # Группы
    for room_id, group in groups.items():
        if username in group['members']:
            user_rooms.append({
                'id': room_id,
                'name': group['name'],
                'type': 'group',
                'avatar': '👥',
                'invite_code': group['invite_code']
            })
    
    return render_template('chat.html', 
                         username=username, 
                         rooms=user_rooms,
                         current_room=None)

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    if username and username.strip():
        session['username'] = username.strip()
        return redirect(url_for('chat'))
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    username = session.pop('username', None)
    if username and username in users:
        for room in users[username].get('rooms', []):
            leave_room(room)
        del users[username]
        socketio.emit('user_left', {'username': username}, broadcast=True)
        socketio.emit('user_list', list(users.keys()), broadcast=True)
    return redirect(url_for('index'))

@app.route('/api/users')
def get_users():
    """Получить список всех пользователей онлайн"""
    if 'username' not in session:
        return jsonify([])
    online_users = [u for u in users.keys() if u != session['username']]
    return jsonify(online_users)

@app.route('/api/start_private/<other_user>')
def start_private(other_user):
    """Начать приватный чат"""
    if 'username' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    username = session['username']
    room_id = get_or_create_private_room(username, other_user)
    
    # Проверяем, существует ли уже этот чат в списке комнат пользователя
    room_exists = False
    for room in private_chats.values():
        if username in room['users'] and other_user in room['users']:
            room_exists = True
            break
    
    return jsonify({
        'room_id': room_id, 
        'other_user': other_user,
        'exists': room_exists
    })

@app.route('/api/create_group', methods=['POST'])
def create_group_api():
    """Создать новую группу"""
    if 'username' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    group_name = data.get('name')
    members = data.get('members', [])
    
    username = session['username']
    all_members = list(set([username] + members))
    
    room_id, invite_code = create_group(group_name, username, all_members)
    
    return jsonify({
        'room_id': room_id,
        'name': group_name,
        'invite_code': invite_code
    })

@app.route('/api/join_group/<invite_code>')
def join_group(invite_code):
    """Присоединиться к группе по коду"""
    if 'username' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    username = session['username']
    
    for room_id, group in groups.items():
        if group['invite_code'] == invite_code.upper():
            if username not in group['members']:
                group['members'].append(username)
            return jsonify({'room_id': room_id, 'name': group['name']})
    
    return jsonify({'error': 'Group not found'}), 404

@app.route('/api/group_info/<room_id>')
def group_info(room_id):
    """Получить информацию о группе"""
    if room_id in groups:
        group = groups[room_id]
        return jsonify({
            'name': group['name'],
            'members': group['members'],
            'creator': group['creator'],
            'invite_code': group['invite_code']
        })
    return jsonify({'error': 'Group not found'}), 404

@socketio.on('join_room')
def handle_join_room(data):
    """Присоединиться к комнате чата"""
    room_id = data['room']
    username = session.get('username')
    
    if username:
        join_room(room_id)
        
        # Сохраняем информацию о пользователе
        if username not in users:
            users[username] = {'sid': request.sid, 'rooms': []}
        if room_id not in users[username]['rooms']:
            users[username]['rooms'].append(room_id)
        
        # Отправляем историю сообщений
        if room_id in messages:
            for msg in messages[room_id]:
                emit('message_history', msg, to=request.sid)

@socketio.on('leave_room')
def handle_leave_room(data):
    """Покинуть комнату"""
    room_id = data['room']
    username = session.get('username')
    
    if username:
        leave_room(room_id)
        if username in users and room_id in users[username]['rooms']:
            users[username]['rooms'].remove(room_id)

@socketio.on('private_message')
def handle_private_message(data):
    """Отправить личное сообщение"""
    username = session.get('username')
    if not username:
        return
    
    room_id = data['room_id']
    msg_type = data.get('type', 'text')
    
    msg_data = {
        'username': username,
        'timestamp': datetime.now().strftime('%H:%M'),
        'type': msg_type
    }
    
    if msg_type == 'text':
        msg_data['text'] = data.get('text', '')
    elif msg_type == 'image':
        msg_data['image'] = data.get('image', '')
    elif msg_type == 'video':
        msg_data['video'] = data.get('video', '')
    
    if room_id not in messages:
        messages[room_id] = []
    
    messages[room_id].append(msg_data)
    if len(messages[room_id]) > 200:
        messages[room_id].pop(0)
    
    emit('new_message', msg_data, to=room_id)

@socketio.on('connect')
def handle_connect():
    username = session.get('username')
    if username:
        users[username] = {'sid': request.sid, 'rooms': []}
        emit('user_list', list(users.keys()), broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    username = None
    for u, data in users.items():
        if data['sid'] == request.sid:
            username = u
            break
    
    if username:
        del users[username]
        emit('user_list', list(users.keys()), broadcast=True)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)