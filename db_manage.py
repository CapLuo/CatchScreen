"""
数据库管理工具
------------
用于管理 SQLite 数据库的脚本
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")


def init_db():
    """初始化数据库表"""
    db = sqlite3.connect(DB_PATH)
    cursor = db.cursor()
    
    # 创建文件夹表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS folders (
            ip TEXT PRIMARY KEY,
            remark TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 创建视频文件表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_size INTEGER,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (ip) REFERENCES folders(ip) ON DELETE CASCADE
        )
    ''')
    
    # 创建索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_videos_ip ON videos(ip)')
     # ✅ 插入一条默认记录（仅在表为空时）
    cursor.execute('''
        INSERT INTO folders (ip, remark)
        VALUES (?, ?)
        ''', ('172.16.0.195', '测试文件夹'))

    db.commit()
    db.close()
    print("✅ 数据库初始化完成")


def list_all():
    """列出所有数据"""
    db = sqlite3.connect(DB_PATH)
    cursor = db.cursor()
    
    print("\n📁 文件夹列表:")
    print("-" * 60)
    cursor.execute('SELECT ip, remark, created_at FROM folders ORDER BY ip')
    folders = cursor.fetchall()
    
    if not folders:
        print("暂无数据")
    else:
        for ip, remark, created in folders:
            print(f"IP: {ip}")
            print(f"  备注: {remark}")
            print(f"  创建时间: {created}")
            print()
    
    print("\n📹 视频文件列表:")
    print("-" * 60)
    cursor.execute('SELECT ip, filename, file_size, uploaded_at FROM videos ORDER BY uploaded_at DESC')
    videos = cursor.fetchall()
    
    if not videos:
        print("暂无视频")
    else:
        for ip, filename, size, uploaded in videos:
            size_mb = size / (1024 * 1024) if size else 0
            print(f"IP: {ip} | 文件: {filename} | 大小: {size_mb:.2f}MB | 上传时间: {uploaded}")
    
    db.close()


def clear_db():
    """清空数据库"""
    confirm = input("⚠️  确定要清空所有数据吗？(yes/no): ")
    if confirm.lower() != 'yes':
        print("已取消")
        return
    
    db = sqlite3.connect(DB_PATH)
    cursor = db.cursor()
    
    cursor.execute('DELETE FROM videos')
    cursor.execute('DELETE FROM folders')
    
    db.commit()
    db.close()
    print("✅ 数据库已清空")


def show_stats():
    """显示统计信息"""
    db = sqlite3.connect(DB_PATH)
    cursor = db.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM folders')
    folder_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM videos')
    video_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT SUM(file_size) FROM videos')
    total_size = cursor.fetchone()[0] or 0
    total_size_mb = total_size / (1024 * 1024)
    
    print(f"\n📊 数据库统计:")
    print(f"文件夹数量: {folder_count}")
    print(f"视频数量: {video_count}")
    print(f"总大小: {total_size_mb:.2f} MB")
    
    db.close()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("""
数据库管理工具
------------
用法: python db_manage.py <命令>

命令:
  init     - 初始化数据库
  list     - 列出所有数据
  stats    - 显示统计信息
  clear    - 清空数据库
        """)
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "init":
        init_db()
    elif command == "list":
        list_all()
    elif command == "stats":
        show_stats()
    elif command == "clear":
        clear_db()
    else:
        print(f"未知命令: {command}")

