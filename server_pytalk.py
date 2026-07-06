import socketio
import eventlet
import random
import string
import uuid
from datetime import datetime

sio = socketio.Server(cors_allowed_origins='*')
app = socketio.WSGIApp(sio)

# ------------------ Dữ liệu ------------------
registered_users = {
    "admin": {"password": "123", "email": "admin@pytalk.com", "saved_rooms": []}
}
sid_to_username = {}
rooms_data = {}  # room_id -> {name, mode, users, messages}

def generate_room_id():
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if code not in rooms_data:
            return code

def generate_message_id():
    return str(uuid.uuid4())

def get_username(sid):
    return sid_to_username.get(sid)

def timestamp_now():
    return datetime.now().isoformat()

# ------------------ Sự kiện kết nối ------------------
@sio.event
def connect(sid, environ):
    print(f"[+] Kết nối: {sid}")

@sio.event
def disconnect(sid):
    username = get_username(sid)
    if username:
        empty_rooms = []
        for rid, room in rooms_data.items():
            if username in room['users']:
                room['users'].remove(username)
                sio.emit('system_message', {
                    'sender': 'Hệ Thống',
                    'content': f'--- {username} đã ngắt kết nối ---'
                }, room=rid)
                if not room['users']:
                    empty_rooms.append(rid)
                else:
                    update_room_and_lobby(rid)
        for rid in empty_rooms:
            del rooms_data[rid]
        del sid_to_username[sid]
        broadcast_rooms_list()

# ------------------ Xác thực ------------------
@sio.on('login')
def handle_login(sid, data):
    username = data.get('username')
    password = data.get('password')
    if username in registered_users and registered_users[username]['password'] == password:
        sid_to_username[sid] = username
        return {"status": "success"}
    return {"status": "error", "message": "Sai tài khoản hoặc mật khẩu!"}

@sio.on('register')
def handle_register(sid, data):
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    if username in registered_users:
        return {"status": "error", "message": "Tên tài khoản đã tồn tại!"}
    registered_users[username] = {
        "password": password,
        "email": email,
        "saved_rooms": []
    }
    print(f"[Database] Đăng ký: {username}")
    return {"status": "success"}

@sio.on('forgot_password')
def handle_forgot_password(sid, data):
    identity = data.get('identity')
    for u, info in registered_users.items():
        if u == identity or info['email'] == identity:
            return {"status": "success", "message": f"Đã gửi link đặt lại mật khẩu tới {info['email']}"}
    return {"status": "error", "message": "Không tìm thấy tài khoản!"}

# ------------------ Phòng ------------------
@sio.on('create_room')
def handle_create_room(sid, data):
    name = data.get('room_name')
    mode = data.get('room_mode')
    room_id = generate_room_id()
    rooms_data[room_id] = {
        'name': name,
        'mode': mode,
        'users': [],
        'messages': []
    }
    print(f"[Room] Tạo phòng '{name}' [{room_id}]")
    return {"status": "success", "room_id": room_id}

@sio.on('join_room')
def handle_join_room(sid, data):
    room_id = data.get('room_id')
    username = get_username(sid)
    if not username:
        return {"status": "error", "message": "Chưa đăng nhập!"}
    if room_id not in rooms_data:
        return {"status": "error", "message": "Phòng không tồn tại!"}
    room = rooms_data[room_id]
    if username not in room['users']:
        room['users'].append(username)
    sio.enter_room(sid, room_id)
    # Gửi lịch sử tin nhắn
    sio.emit('message_history', {'messages': room['messages']}, to=sid)
    sio.emit('system_message', {
        'sender': 'Hệ Thống',
        'content': f'--- {username} đã tham gia phòng ---'
    }, room=room_id)
    update_room_and_lobby(room_id)
    return {"status": "success"}

@sio.on('leave_room')
def handle_leave_room(sid, data):
    room_id = data.get('room_id')
    username = get_username(sid)
    if room_id in rooms_data and username in rooms_data[room_id]['users']:
        room = rooms_data[room_id]
        room['users'].remove(username)
        sio.leave_room(sid, room_id)
        sio.emit('system_message', {
            'sender': 'Hệ Thống',
            'content': f'--- {username} đã rời phòng ---'
        }, room=room_id)
        if not room['users']:
            del rooms_data[room_id]
        else:
            update_room_and_lobby(room_id)
        broadcast_rooms_list()

# ------------------ Tin nhắn ------------------
@sio.on('send_message')
def handle_send_message(sid, data):
    room_id = data.get('room_id')
    content = data.get('content', '')
    msg_type = data.get('type', 'text')
    file_data = data.get('file_data', None)
    file_name = data.get('file_name', None)
    reply_to = data.get('reply_to', None)

    username = get_username(sid)
    if not username or room_id not in rooms_data:
        return

    message = {
        'id': generate_message_id(),
        'sender': username,
        'content': content,
        'type': msg_type,
        'file_data': file_data,
        'file_name': file_name,
        'timestamp': timestamp_now(),
        'reply_to': reply_to,
        'edited': False,
        'deleted': False
    }
    rooms_data[room_id]['messages'].append(message)
    sio.emit('new_message', message, room=room_id)

@sio.on('edit_message')
def handle_edit_message(sid, data):
    room_id = data.get('room_id')
    msg_id = data.get('message_id')
    new_content = data.get('content')
    username = get_username(sid)
    if not username or room_id not in rooms_data:
        return
    room = rooms_data[room_id]
    for msg in room['messages']:
        if msg['id'] == msg_id and msg['sender'] == username:
            msg['content'] = new_content
            msg['edited'] = True
            sio.emit('message_edited', {
                'message_id': msg_id,
                'new_content': new_content
            }, room=room_id)
            break

@sio.on('delete_message')
def handle_delete_message(sid, data):
    room_id = data.get('room_id')
    msg_id = data.get('message_id')
    username = get_username(sid)
    if not username or room_id not in rooms_data:
        return
    room = rooms_data[room_id]
    for msg in room['messages']:
        if msg['id'] == msg_id and msg['sender'] == username:
            msg['deleted'] = True
            sio.emit('message_deleted', {
                'message_id': msg_id,
                'sender': username
            }, room=room_id)
            break

# ------------------ Lưu phòng yêu thích ------------------
@sio.on('get_saved_rooms')
def handle_get_saved_rooms(sid):
    username = get_username(sid)
    if username:
        saved = registered_users[username].get('saved_rooms', [])
        sio.emit('receive_saved_rooms', {'rooms': saved}, to=sid)

@sio.on('save_rooms')
def handle_save_rooms(sid, data):
    username = get_username(sid)
    if username:
        registered_users[username]['saved_rooms'] = data.get('rooms', [])

# ------------------ Hàm tiện ích ------------------
def update_room_and_lobby(room_id):
    if room_id in rooms_data:
        room = rooms_data[room_id]
        sio.emit('update_room_info', {
            'room_name': room['name'],
            'room_id': room_id,
            'users': room['users']
        }, room=room_id)
    broadcast_rooms_list()

def broadcast_rooms_list():
    rooms = [{'id': rid, 'name': r['name'], 'mode': r['mode']} for rid, r in rooms_data.items()]
    sio.emit('refresh_rooms_list', {'rooms': rooms})

# ------------------ Xử lý request không phải socket.io ------------------
def my_app(environ, start_response):
    path = environ.get('PATH_INFO', '')
    if path.startswith('/socket.io'):
        return app(environ, start_response)
    else:
        start_response('200 OK', [('Content-Type', 'text/plain')])
        return [b'PyTalk Server is running']

if __name__ == '__main__':
    print("🚀 Server PyTalk nâng cấp đang chạy cổng 5000...")
    # Chạy trên 0.0.0.0 để cho phép truy cập từ bên ngoài (nếu cần)
    eventlet.wsgi.server(eventlet.listen(('0.0.0.0', 5000)), my_app)
