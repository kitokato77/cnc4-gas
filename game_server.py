import http.server
import socketserver
import json
import threading
import uuid
import urllib.parse
import os
import time

# In-memory storage untuk rooms
rooms_storage = {}
storage_lock = threading.Lock()

class GameServerHandler(http.server.BaseHTTPRequestHandler):
    def _set_headers(self, status=200):
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers()

    def log_message(self, format, *args):
        # Suppress default logging untuk mengurangi noise
        pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        
        if self.path == '/create_room':
            player = data.get('player')
            if not player:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Missing player in request'}).encode())
                return
            
            room_id = str(uuid.uuid4())[:8]
            room = {
                'players': [player],
                'ready': {player: False},
                'board': [[0]*7 for _ in range(6)],
                'turn': 0,
                'winner': None,
                'created_at': time.time()
            }
            save_room(room_id, room)
            self._set_headers()
            self.wfile.write(json.dumps({'room_id': room_id}).encode())
            print(f"Room {room_id} created by {player}")
            
        elif self.path == '/join_room':
            player = data.get('player')
            room_id = data.get('room_id')
            room = get_room(room_id)
            
            if not room:
                self._set_headers(404)
                self.wfile.write(json.dumps({'error': 'Room not found'}).encode())
                return
            
            if len(room['players']) >= 2:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Room already full'}).encode())
                return
            
            if player in room['players']:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Player already in room'}).encode())
                return
            
            room['players'].append(player)
            room['ready'][player] = False
            save_room(room_id, room)
            self._set_headers()
            self.wfile.write(json.dumps({'room_id': room_id, 'success': True}).encode())
            print(f"Player {player} joined room {room_id}")
            
        elif self.path == '/quick_join':
            player = data.get('player')
            found = False
            
            with storage_lock:
                # Cari room yang hanya ada 1 player
                for room_id, room in rooms_storage.items():
                    if room and len(room['players']) == 1 and player not in room['players']:
                        room['players'].append(player)
                        room['ready'][player] = False
                        save_room(room_id, room)
                        self._set_headers()
                        self.wfile.write(json.dumps({'room_id': room_id}).encode())
                        found = True
                        print(f"Player {player} quick joined room {room_id}")
                        break
            
            if not found:
                # Buat room baru jika tidak ada room yang bisa di-join
                room_id = str(uuid.uuid4())[:8]
                room = {
                    'players': [player],
                    'ready': {player: False},
                    'board': [[0]*7 for _ in range(6)],
                    'turn': 0,
                    'winner': None,
                    'created_at': time.time()
                }
                save_room(room_id, room)
                self._set_headers()
                self.wfile.write(json.dumps({'room_id': room_id}).encode())
                print(f"New room {room_id} created for quick join by {player}")
                
        elif self.path == '/set_ready':
            player = data.get('player')
            room_id = data.get('room_id')
            room = get_room(room_id)
            
            if not room or player not in room['players']:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid room or player'}).encode())
                return
            
            room['ready'][player] = True
            all_ready = all(room['ready'].values()) and len(room['players']) == 2
            save_room(room_id, room)
            self._set_headers()
            self.wfile.write(json.dumps({'all_ready': all_ready}).encode())
            print(f"Player {player} ready in room {room_id}, all_ready: {all_ready}")
            
        elif self.path == '/make_move':
            player = data.get('player')
            room_id = data.get('room_id')
            col = data.get('col')
            room = get_room(room_id)
            
            if not room:
                self._set_headers(404)
                self.wfile.write(json.dumps({'error': 'Room not found'}).encode())
                return
            
            if room['winner'] is not None:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Game over'}).encode())
                return
            
            try:
                player_idx = room['players'].index(player)
            except ValueError:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Player not in room'}).encode())
                return
            
            if room['turn'] != player_idx:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Not your turn'}).encode())
                return
            
            # Validasi kolom
            if not (0 <= col < 7):
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid column'}).encode())
                return
            
            # Cari row kosong dari bawah
            for row in reversed(range(6)):
                if room['board'][row][col] == 0:
                    room['board'][row][col] = player_idx + 1
                    if check_win(room['board'], row, col, player_idx + 1):
                        room['winner'] = player
                        print(f"Player {player} wins in room {room_id}!")
                    else:
                        room['turn'] = 1 - room['turn']
                    save_room(room_id, room)
                    self._set_headers()
                    self.wfile.write(json.dumps({'success': True, 'winner': room['winner']}).encode())
                    return
            
            # Kolom penuh
            self._set_headers(400)
            self.wfile.write(json.dumps({'error': 'Column full'}).encode())
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({'error': 'Not found'}).encode())

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        
        # Healthcheck endpoint untuk Railway
        if path == '/' or path == '/health':
            self._set_headers()
            response = {
                'status': 'ok', 
                'service': 'Connect Four Game Server',
                'rooms_count': len(rooms_storage),
                'timestamp': str(int(time.time()))
            }
            self.wfile.write(json.dumps(response).encode())
            return
        
        # Status endpoint untuk monitoring
        elif path == '/status':
            self._set_headers()
            active_rooms = sum(1 for room in rooms_storage.values() if room and len(room['players']) > 0)
            response = {
                'total_rooms': len(rooms_storage),
                'active_rooms': active_rooms,
                'uptime': str(int(time.time()))
            }
            self.wfile.write(json.dumps(response).encode())
            return
            
        elif path == '/lobby_status':
            room_id = query.get('room_id', [None])[0]
            room = get_room(room_id)
            if not room:
                self._set_headers(404)
                self.wfile.write(json.dumps({'error': 'Room not found'}).encode())
                return
            self._set_headers()
            self.wfile.write(json.dumps({'players': room['players'], 'ready': room['ready']}).encode())
            
        elif path == '/game_state':
            room_id = query.get('room_id', [None])[0]
            room = get_room(room_id)
            if not room:
                self._set_headers(404)
                self.wfile.write(json.dumps({'error': 'Room not found'}).encode())
                return
            self._set_headers()
            self.wfile.write(json.dumps({
                'board': room['board'], 
                'turn': room['turn'], 
                'winner': room['winner'],
                'players': room['players']
            }).encode())
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({'error': 'Not found'}).encode())

def get_room(room_id):
    """Ambil room dari storage"""
    if not room_id:
        return None
    with storage_lock:
        return rooms_storage.get(room_id)

def save_room(room_id, room):
    """Simpan room ke storage"""
    with storage_lock:
        rooms_storage[room_id] = room

def check_win(board, row, col, player):
    """Check apakah ada kemenangan setelah move terakhir"""
    def count(dx, dy):
        cnt = 0
        x, y = col, row
        while 0 <= x < 7 and 0 <= y < 6 and board[y][x] == player:
            cnt += 1
            x += dx
            y += dy
        return cnt - 1
    
    directions = [(1,0), (0,1), (1,1), (1,-1)]  # horizontal, vertical, diagonal
    for dx, dy in directions:
        total = 1 + count(dx, dy) + count(-dx, -dy)
        if total >= 4:
            return True
    return False

def cleanup_old_rooms():
    """Background task untuk membersihkan room lama"""
    import threading
    import time
    
    def cleanup():
        while True:
            try:
                current_time = time.time()
                with storage_lock:
                    rooms_to_delete = []
                    for room_id, room in rooms_storage.items():
                        # Hapus room yang sudah 1 jam tidak aktif
                        if room and 'created_at' in room:
                            if current_time - room['created_at'] > 3600:  # 1 jam
                                rooms_to_delete.append(room_id)
                    
                    for room_id in rooms_to_delete:
                        del rooms_storage[room_id]
                        print(f"Cleaned up old room: {room_id}")
                
                time.sleep(300)  # Cleanup setiap 5 menit
            except Exception as e:
                print(f"Cleanup error: {e}")
                time.sleep(60)
    
    cleanup_thread = threading.Thread(target=cleanup, daemon=True)
    cleanup_thread.start()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT', 5001)))
    args = parser.parse_args()
    
    print(f"Starting Connect Four Game Server on port {args.port}")
    print("Using in-memory storage (no Redis required)")
    
    # Start background cleanup task
    cleanup_old_rooms()
    
    try:
        with socketserver.ThreadingTCPServer(("", args.port), GameServerHandler) as httpd:
            print(f"✅ Server ready! Access healthcheck at http://localhost:{args.port}/")
            print(f"📊 Status endpoint: http://localhost:{args.port}/status")
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Server stopped")
    except Exception as e:
        print(f"❌ Server error: {e}")
