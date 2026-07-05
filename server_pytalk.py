import socketio
import eventlet
import random
import string

sio = socketio.Server(cors_allowed_origins='*')
app = socketio.WSGIApp(sio)

# Cơ sở dữ liệu tập trung trên Server
registered_users = {
    "admin": {
        "password": "123",
        "email": "admin@pytalk.com",
        "saved_rooms": []          # Danh sách mã phòng đã lưu
    }
}
sid_to_username = {}
rooms_data = {}

def generate_room_id():
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if code not in rooms_data:
            return code

@sio.event
def connect(sid, environ):
    print(f"[+] Thiết bị kết nối Socket ID: {sid}")

@sio.on('login')
def handle_login(sid, data):
    username = data.get('username')
    password = data.get('password')
    if username in registered_users and registered_users[username]['password'] == password:
        sid_to_username[sid] = username
        return {"status": "success"}
    return {"status": "error", "message": "Sai tài khoản hoặc mật khẩu hệ thống!"}

@sio.on('register')
def handle_register(sid, data):
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    if username in registered_users:
        return {"status": "error", "message": "Tên tài khoản này đã tồn tại trên Server!"}
    registered_users[username] = {
        "password": password,
        "email": email,
        "saved_rooms": []          # Khởi tạo danh sách rỗng
    }
    print(f"[Database Server] Đăng ký mới thành công: {username}")
    return {"status": "success"}

@sio.on('forgot_password')
def handle_forgot_password(sid, data):
    identity = data.get('identity')
    for u, info in registered_users.items():
        if u == identity or info['email'] == identity:
            return {"status": "success", "message": f"Hệ thống đã gửi liên kết đặt lại mật khẩu về email đăng ký của tài khoản này."}
    return {"status": "error", "message": "Không tìm thấy thông tin tài khoản trên Máy Chủ!"}

@sio.on('create_room')
def handle_create_room(sid, data):
    name = data.get('room_name')
    mode = data.get('room_mode')
    room_id = generate_room_id()
    rooms_data[room_id] = {'name': name, 'mode': mode, 'users': []}
    print(f"[Room] Phòng '{name}' [{room_id}] đã khởi tạo chế độ: {mode}")
    return {"status": "success", "room_id": room_id}

@sio.on('join_room')
def handle_join_room(sid, data):
    room_id = data.get('room_id')
    username = sid_to_username.get(sid)
    if not username:
        return {"status": "error", "message": "Chưa xác thực!"}
    if room_id not in rooms_data:
        return {"status": "error", "message": "Mã phòng không tồn tại hoặc đã đóng!"}
    
    room = rooms_data[room_id]
    if username not in room['users']:
        room['users'].append(username)
    
    sio.enter_room(sid, room_id)
    sio.emit('receive_message', {'sender': 'Hệ Thống', 'content': f"--- {username} đã kết nối vào phòng chat ---"}, room=room_id)
    
    update_room_and_lobby(room_id)
    return {"status": "success"}

@sio.on('leave_room')
def handle_leave_room(sid, data):
    room_id = data.get('room_id')
    username = sid_to_username.get(sid)
    if room_id in rooms_data and username in rooms_data[room_id]['users']:
        rooms_data[room_id]['users'].remove(username)
        sio.leave_room(sid, room_id)
        sio.emit('receive_message', {'sender': 'Hệ Thống', 'content': f"--- {username} đã rời phòng chat ---"}, room=room_id)
        
        if not rooms_data[room_id]['users']:
            del rooms_data[room_id]
        else:
            update_room_and_lobby(room_id)
        broadcast_rooms_list()

@sio.on('send_message')
def handle_message(sid, data):
    room_id = data.get('room_id')
    content = data.get('content')
    sio.emit('receive_message', {'sender': sid_to_username.get(sid, "Ẩn danh"), 'content': content}, room=room_id)

@sio.on('get_rooms_list')
def handle_get_rooms(sid):
    send_rooms_to_one(sid)

# ---------- CÁC SỰ KIỆN ĐỒNG BỘ PHÒNG ĐÃ LƯU ----------
@sio.on('get_saved_rooms')
def handle_get_saved_rooms(sid):
    username = sid_to_username.get(sid)
    if username:
        saved = registered_users[username].get('saved_rooms', [])
        sio.emit('receive_saved_rooms', {'rooms': saved}, to=sid)

@sio.on('save_rooms')
def handle_save_rooms(sid, data):
    username = sid_to_username.get(sid)
    if not username:
        return
    rooms = data.get('rooms', [])
    registered_users[username]['saved_rooms'] = rooms
    print(f"[Server] Đã lưu danh sách phòng yêu thích của {username}: {rooms}")
    # Không cần trả về, client đã tự cập nhật

# -----------------------------------------------------

def update_room_and_lobby(room_id):
    if room_id in rooms_data:
        sio.emit('update_room_info', {
            'room_name': rooms_data[room_id]['name'],
            'room_id': room_id,
            'users': rooms_data[room_id]['users']
        }, room=room_id)
    broadcast_rooms_list()

def broadcast_rooms_list():
    rooms = [{'id': rid, 'name': r['name'], 'mode': r['mode']} for rid, r in rooms_data.items()]
    sio.emit('refresh_rooms_list', {'rooms': rooms})

def send_rooms_to_one(sid):
    rooms = [{'id': rid, 'name': r['name'], 'mode': r['mode']} for rid, r in rooms_data.items()]
    sio.emit('refresh_rooms_list', {'rooms': rooms}, to=sid)

@sio.event
def disconnect(sid):
    username = sid_to_username.get(sid)
    if username:
        empty_rooms = []
        for rid, room in rooms_data.items():
            if username in room['users']:
                room['users'].remove(username)
                sio.emit('receive_message', {'sender': 'Hệ Thống', 'content': f"--- {username} đột ngột ngắt kết nối ---"}, room=rid)
                if not room['users']:
                    empty_rooms.append(rid)
                else:
                    sio.emit('update_room_info', {'room_name': room['name'], 'room_id': rid, 'users': room['users']}, room=rid)
        for rid in empty_rooms:
            del rooms_data[rid]
        del sid_to_username[sid]
        broadcast_rooms_list()

if __name__ == '__main__':
    print("🚀 Server PyTalk Đang Hoạt Động Trên Cổng 5000...")
    eventlet.wsgi.server(eventlet.listen(('127.0.0.1', 5000)), app)
