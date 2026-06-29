import os
import sqlite3
from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import eventlet

app = Flask(__name__)
app.config['SECRET_KEY'] = 'pytalk_secure_key_2026'

# Cấu hình Socket.IO hỗ trợ đa nền tảng và chạy bất đồng bộ bằng eventlet
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

DB_FILE = 'pytalk_database.db'

def get_db_connection():
    """Khởi tạo kết nối tới cơ sở dữ liệu trên Server"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # Giúp truy xuất dữ liệu theo tên cột dễ dàng
    return conn

def init_db():
    """Tạo các bảng lưu trữ tập trung trên Server nếu chưa tồn tại"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Bảng quản lý tài khoản người dùng
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            email TEXT NOT NULL
        )
    ''')
    
    # 2. Bảng quản lý toàn bộ lịch sử trò chuyện của các phòng
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    print("[+] Cơ sở dữ liệu PyTalk đã được đồng bộ và sẵn sàng.")

# Bộ nhớ đệm quản lý các phòng chat và thành viên đang ONLINE thực tế
# Cấu trúc: { room_id: { "name": room_name, "mode": mode, "users": [] } }
active_rooms = {}

# Ánh xạ để biết thiết bị kết nối (sid) nào thuộc về tài khoản (username) nào
sid_to_username = {}

# ==================== XỬ LÝ ĐĂNG KÝ & ĐĂNG NHẬP ====================

@socketio.on('register')
def handle_register(data):
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    
    if not username or not password or not email:
        return {'status': 'error', 'message': 'Dữ liệu nhập vào không hợp lệ.'}
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Ghi tài khoản mới vào cơ sở dữ liệu server
        cursor.execute('INSERT INTO users (username, password, email) VALUES (?, ?, ?)', (username, password, email))
        conn.commit()
        print(f"[+] Tài khoản mới đăng ký thành công: {username}")
        return {'status': 'success', 'message': 'Tạo tài khoản thành công!'}
    except sqlite3.IntegrityError:
        return {'status': 'error', 'message': 'Tên đăng nhập này đã tồn tại trên Server!'}
    finally:
        conn.close()

@socketio.on('login')
def handle_login(data):
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    # Kiểm tra tài khoản từ cơ sở dữ liệu server
    cursor.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, password))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        # Ghi nhận thiết bị kết nối này thuộc về user vừa đăng nhập
        sid_to_username[request.sid] = username
        print(f"[🟢] Người dùng đăng nhập thành công: {username}")
        return {'status': 'success'}
        
    return {'status': 'error', 'message': 'Sai tài khoản hoặc mật khẩu hệ thống.'}


# ==================== QUẢN LÝ PHÒNG CHAT TRÊN SẢNH CHỜ ====================

@socketio.on('create_room')
def handle_create_room(data):
    import random, string
    # Tạo mã phòng ngẫu nhiên 6 ký tự viết hoa
    room_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    room_name = data.get('room_name', 'Phòng mới')
    room_mode = data.get('room_mode', 'Mã phòng (Tự do)')
    
    active_rooms[room_id] = {
        'name': room_name,
        'mode': room_mode,
        'users': []
    }
    emit_rooms_list() # Cập nhật danh sách phòng mới cho toàn sảnh chờ
    return {'status': 'success', 'room_id': room_id}

@socketio.on('get_rooms_list')
def handle_get_rooms():
    emit_rooms_list(broadcast=False)

def emit_rooms_list(broadcast=True):
    rooms_data = [{'id': k, 'name': v['name'], 'mode': v['mode']} for k, v in active_rooms.items()]
    emit('refresh_rooms_list', {'rooms': rooms_data}, broadcast=broadcast)


# ==================== XỬ LÝ TRÒ CHUYỆN REAL-TIME & LỊCH SỬ ====================

@socketio.on('join_room')
def handle_join_room(data):
    room_id = data.get('room_id')
    username = sid_to_username.get(request.sid)
    
    if not username:
        return {'status': 'error', 'message': 'Yêu cầu xác thực tài khoản bị từ chối.'}
        
    # Nếu phòng chưa hoạt động trong phiên làm việc hiện tại, khởi tạo lại cấu hình bộ nhớ tạm
    if room_id not in active_rooms:
        active_rooms[room_id] = {'name': f"Phòng {room_id}", 'mode': "Tự do", 'users': []}
        
    if username not in active_rooms[room_id]['users']:
        active_rooms[room_id]['users'].append(username)
        
    join_room(room_id)
    
    # 1. Cập nhật giao diện phòng chat hiện tại cho User
    emit('update_room_info', {
        'room_name': active_rooms[room_id]['name'],
        'room_id': room_id,
        'users': active_rooms[room_id]['users']
    })
    
    # 🔥 LẤY LỊCH SỬ TIN NHẮN TỪ SERVER ĐẨY VỀ CHO USER VỪA VÀO PHÒNG
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT sender, content FROM messages WHERE room_id = ? ORDER BY id ASC', (room_id,))
    chat_history = cursor.fetchall()
    conn.close()
    
    for msg in chat_history:
        emit('receive_message', {'sender': msg['sender'], 'content': msg['content']})
        
    # 2. Cập nhật lại danh sách thành viên online cho tất cả những người khác trong phòng
    emit('update_room_info', {
        'room_name': active_rooms[room_id]['name'],
        'room_id': room_id,
        'users': active_rooms[room_id]['users']
    }, room=room_id)
    
    return {'status': 'success'}

@socketio.on('send_message')
def handle_send_message(data):
    room_id = data.get('room_id')
    content = data.get('content', '').strip()
    username = sid_to_username.get(request.sid)
    
    if room_id and content and username:
        # 🔥 LƯU TIN NHẮN VÀO CƠ SỞ DỮ LIỆU TRÊN SERVER TRƯỚC KHI PHÁT ĐI
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO messages (room_id, sender, content) VALUES (?, ?, ?)', (room_id, username, content))
        conn.commit()
        conn.close()
        
        # Phát tin nhắn real-time tới mọi thiết bị trong phòng chat
        emit('receive_message', {'sender': username, 'content': content}, room=room_id)

@socketio.on('leave_room')
def handle_leave_room(data):
    room_id = data.get('room_id')
    username = sid_to_username.get(request.sid)
    
    if room_id in active_rooms and username in active_rooms[room_id]['users']:
        active_rooms[room_id]['users'].remove(username)
        leave_room(room_id)
        
        # Cập nhật lại danh sách người dùng cho phòng chat
        emit('update_room_info', {
            'room_name': active_rooms[room_id]['name'],
            'room_id': room_id,
            'users': active_rooms[room_id]['users']
        }, room=room_id)

@socketio.on('disconnect')
def handle_disconnect():
    username = sid_to_username.get(request.sid)
    if username:
        # Quét dọn, xóa trạng thái online của người dùng khỏi tất cả các phòng khi họ tắt App
        for room_id, room_info in list(active_rooms.items()):
            if username in room_info['users']:
                room_info['users'].remove(username)
                emit('update_room_info', {
                    'room_name': room_info['name'],
                    'room_id': room_id,
                    'users': room_info['users']
                }, room=room_id)
        del sid_to_username[request.sid]
        print(f"[🔴] Người dùng đã ngắt kết nối: {username}")


if __name__ == '__main__':
    # Tự động tạo cấu hình bảng dữ liệu ngay khi khởi chạy
    init_db()
    
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 PyTalk Server đang chạy ổn định tại cổng {port}...")
    eventlet.wsgi.server(eventlet.listen(('0.0.0.0', port)), app)
