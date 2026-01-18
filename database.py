"""
Database module for Telegram Bot
Handles all database operations for users, generations, and referrals
"""

import os
import psycopg2
from datetime import datetime, date
from typing import Optional, Dict, List, Tuple
from contextlib import contextmanager

# Railway'dagi DATABASE_URL hamma ma'lumotni o'zi ichiga oladi
DATABASE_URL = os.environ.get('DATABASE_URL')

# Faqat shunday ulanasiz:
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# ============ DATABASE CONNECTION ============

@contextmanager
def get_connection():
    """Context manager for PostgreSQL connections"""
    # 1. (DATABASE_NAME) qismini butunlay o'chiring
    # 2. sslmode='require' ni qo'shish tavsiya etiladi
    db_url = os.environ.get('DATABASE_URL')
    conn = psycopg2.connect(db_url, sslmode='require')
    
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# ============ DATABASE INITIALIZATION ============

def init_db():
    """Initialize database tables"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                language TEXT DEFAULT 'uz',
                daily_limit BIGINT DEFAULT 2,
                used_today BIGINT DEFAULT 0,
                last_reset DATE,
                total_generations BIGINT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Generations table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS generations (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                doc_type TEXT NOT NULL,
                topic TEXT NOT NULL,
                pages BIGINT NOT NULL,
                design TEXT,
                status TEXT DEFAULT 'pending',
                file_path TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Referrals table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                id BIGSERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                referred_id BIGINT NOT NULL,
                bonus_applied BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_referrer FOREIGN KEY (referrer_id) REFERENCES users (user_id),
                CONSTRAINT fk_referred FOREIGN KEY (referred_id) REFERENCES users (user_id),
                UNIQUE(referrer_id, referred_id)
            )
        ''')

        
        # Create indexes for better performance
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_generations_user_id 
            ON generations(user_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_generations_created_at 
            ON generations(created_at)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_referrals_referrer 
            ON referrals(referrer_id)
        ''')
        
        conn.commit()
        print("✅ Database initialized successfully")


# ============ USER DATABASE CLASS ============

class UserDB:
    """Database operations for users"""
    
    @staticmethod
    def create_user(user_id: int, username: str = None, first_name: str = None) -> Dict:
        """Create a new user or return existing"""
        with get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if user exists
            cursor.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
            existing = cursor.fetchone()
            
            if existing:
                return dict(existing)
            
            # Create new user
            today = date.today().isoformat()
            cursor.execute('''
                INSERT INTO users (user_id, username, first_name, last_reset)
                VALUES ($s, $s, $s, $s)
            ''', (user_id, username, first_name, today))
            
            conn.commit()
            
            # Return created user
            cursor.execute('SELECT * FROM users WHERE user_id = $s', (user_id,))
            return dict(cursor.fetchone())
    
    @staticmethod
    def get_user(user_id: int) -> Optional[Dict]:
        """Get user by ID"""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE user_id = $s', (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    @staticmethod
    def update_language(user_id: int, language: str):
        """Update user's preferred language"""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users 
                SET language = $s, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = $s
            ''', (language, user_id))
            conn.commit()
    
    @staticmethod
    def get_daily_limit(user_id: int) -> Tuple[int, int]:
        """Get remaining and total daily limit"""
        user = UserDB.get_user(user_id)
        if not user:
            return (0, 0)
        
        # Check if need to reset
        UserDB._check_and_reset_limit(user_id)
        
        # Get fresh data after potential reset
        user = UserDB.get_user(user_id)
        remaining = user['daily_limit'] - user['used_today']
        return (remaining, user['daily_limit'])
    
    @staticmethod
    def can_generate(user_id: int) -> bool:
        """Check if user can generate a document"""
        UserDB._check_and_reset_limit(user_id)
        user = UserDB.get_user(user_id)
        return user['used_today'] < user['daily_limit']
    
    @staticmethod
    def use_generation(user_id: int):
        """Mark one generation as used"""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users 
                SET used_today = used_today + 1,
                    total_generations = total_generations + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = $s
            ''', (user_id,))
            conn.commit()
    
    @staticmethod
    def _check_and_reset_limit(user_id: int):
        """Reset daily limit if day changed"""
        user = UserDB.get_user(user_id)
        if not user:
            return
        
        today = date.today().isoformat()
        
        if user['last_reset'] != today:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users 
                    SET used_today = 0, 
                        last_reset = $s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = $s
                ''', (today, user_id))
                conn.commit()
    
    @staticmethod
    def get_all_users() -> List[Dict]:
        """Get all users"""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users ORDER BY created_at DESC')
            return [dict(row) for row in cursor.fetchall()]


# ============ GENERATION DATABASE CLASS ============

class GenerationDB:
    """Database operations for generations"""
    
    @staticmethod
    def create_generation(user_id: int, doc_type: str, topic: str, 
                         pages: int, design: str = None) -> int:
        """Create a new generation record"""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO generations (user_id, doc_type, topic, pages, design, status)
                VALUES ($s, $s, $s, $s, $s, 'pending')
            ''', (user_id, doc_type, topic, pages, design))
            conn.commit()
            return cursor.lastrowid
    
    @staticmethod
    def update_status(generation_id: int, status: str, 
                     file_path: str = None, error_message: str = None):
        """Update generation status"""
        with get_connection() as conn:
            cursor = conn.cursor()
            
            completed_at = datetime.now().isoformat() if status == 'completed' else None
            
            cursor.execute('''
                UPDATE generations 
                SET status = $s, 
                    file_path = $s,
                    error_message = $s,
                    completed_at = $s
                WHERE id = $s
            ''', (status, file_path, error_message, completed_at, generation_id))
            conn.commit()
    
    @staticmethod
    def get_generation(generation_id: int) -> Optional[Dict]:
        """Get generation by ID"""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM generations WHERE id = $s', (generation_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    @staticmethod
    def get_user_generations(user_id: int, limit: int = 10) -> List[Dict]:
        """Get user's recent generations"""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM generations 
                WHERE user_id = $s 
                ORDER BY created_at DESC 
                LIMIT $s
            ''', (user_id, limit))
            return [dict(row) for row in cursor.fetchall()]
    
    @staticmethod
    def get_user_stats(user_id: int) -> Dict:
        """Get user generation statistics"""
        with get_connection() as conn:
            cursor = conn.cursor()
            
            # Total generations
            cursor.execute('''
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                       SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
                FROM generations
                WHERE user_id = $s
            ''', (user_id,))
            
            row = cursor.fetchone()
            return dict(row) if row else {'total': 0, 'completed': 0, 'failed': 0}


# ============ REFERRAL DATABASE CLASS ============

class ReferralDB:
    """Database operations for referrals"""
    
    @staticmethod
    def add_referral(referrer_id: int, referred_id: int) -> bool:
        """Add a referral and give bonus to referrer"""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                
                # Check if referral already exists
                cursor.execute('''
                    SELECT id FROM referrals 
                    WHERE referrer_id = $s AND referred_id = $s
                ''', (referrer_id, referred_id))
                
                if cursor.fetchone():
                    return False  # Already referred
                
                # Add referral
                cursor.execute('''
                    INSERT INTO referrals (referrer_id, referred_id, bonus_applied)
                    VALUES ($s, $s, 1)
                ''', (referrer_id, referred_id))
                
                # Give bonus to referrer (+1 permanent limit)
                cursor.execute('''
                    UPDATE users 
                    SET daily_limit = daily_limit + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = $s
                ''', (referrer_id,))
                
                conn.commit()
                return True
                
        except sqlite3.IntegrityError:
            return False
    
    @staticmethod
    def get_referral_count(referrer_id: int) -> int:
        """Get count of successful referrals"""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) as count 
                FROM referrals 
                WHERE referrer_id = $s
            ''', (referrer_id,))
            row = cursor.fetchone()
            return row['count'] if row else 0
    
    @staticmethod
    def get_referrals(referrer_id: int) -> List[Dict]:
        """Get all referrals for a user"""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT r.*, u.username, u.first_name 
                FROM referrals r
                LEFT JOIN users u ON r.referred_id = u.user_id
                WHERE r.referrer_id = $s
                ORDER BY r.created_at DESC
            ''', (referrer_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    @staticmethod
    def get_referrer(referred_id: int) -> Optional[int]:
        """Get who referred this user"""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT referrer_id 
                FROM referrals 
                WHERE referred_id = $s
            ''', (referred_id,))
            row = cursor.fetchone()
            return row['referrer_id'] if row else None


# ============ COMBINED DATABASE CLASS ============

class Database:
    """Main database class combining all operations"""
    
    def __init__(self):
        self.users = UserDB()
        self.generations = GenerationDB()
        self.referrals = ReferralDB()
    
    # Shortcut methods for common operations
    def create_user(self, user_id: int, username: str = None, first_name: str = None):
        return self.users.create_user(user_id, username, first_name)
    
    def get_user(self, user_id: int):
        return self.users.get_user(user_id)
    
    def can_generate(self, user_id: int):
        return self.users.can_generate(user_id)
    
    def use_generation(self, user_id: int):
        return self.users.use_generation(user_id)
    
    def get_daily_limit(self, user_id: int):
        return self.users.get_daily_limit(user_id)
    
    def add_referral(self, referrer_id: int, referred_id: int):
        return self.referrals.add_referral(referrer_id, referred_id)
    
    def get_referral_count(self, referrer_id: int):
        return self.referrals.get_referral_count(referrer_id)


# ============ GLOBAL DATABASE INSTANCE ============

_db_instance = None

def get_db() -> Database:
    """Get global database instance"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance


# ============ INITIALIZE ON IMPORT ============

if __name__ == "__main__":
    print("Initializing database...")
    init_db()
    print("Database ready!")
else:
    # Auto-initialize when imported
    try:
        init_db()
    except Exception as e:

        print(f"Warning: Could not initialize database: {e}")




