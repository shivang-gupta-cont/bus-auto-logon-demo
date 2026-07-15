import oracledb
import pandas as pd


class OracleDB:
    """
    A reusable Oracle Database client with manual connect/disconnect.
    Supports multiple database connections.

    Usage:
        from oracle_db import OracleDB

        # Using default config
        db = OracleDB()
        db.connect()
        df = db.query("SELECT * FROM sa_stoppoints")
        db.disconnect()

        # Using different DB
        db2 = OracleDB(user="hr", password="hr123", dsn="192.168.1.20:1521/hrdb")
        db2.connect()
        df2 = db2.query("SELECT * FROM employees")
        db2.disconnect()

        # Using context manager (auto connect/disconnect)
        with OracleDB() as db:
            df = db.query("SELECT * FROM sa_stoppoints")
    """

    _CONFIG = {
        "user": "bidb",
        "password": "elcaro",
        "dsn": "localhost:1521/pdb1"
    }

    def __init__(self, user=None, password=None, dsn=None):
        """
        Optional: pass credentials to override default _CONFIG.
        If nothing passed, uses _CONFIG above.
        """
        self.user = user or self._CONFIG["user"]
        self.password = password or self._CONFIG["password"]
        self.dsn = dsn or self._CONFIG["dsn"]
        self._conn = None  # holds active connection (None = disconnected)

    def connect(self):
        """
        Open connection to Oracle DB.
        Call this before running any query.
        """
        if self._conn is not None:
            print("⚠️  Already connected. Call disconnect() first to switch DB.")
            return self

        try:
            self._conn = oracledb.connect(
                user=self.user,
                password=self.password,
                dsn=self.dsn
            )
            print(f"✅ Connected to → {self.dsn} as [{self.user.upper()}]")
        except oracledb.Error as e:
            self._conn = None
            print(f"❌ Connection failed: {e}")

        return self  # allows chaining: db.connect().query(...)

    def disconnect(self):
        """
        Close the active DB connection.
        Always call this when done.
        """
        if self._conn is None:
            print("⚠️  No active connection to close.")
            return

        try:
            self._conn.close()
            print(f"🔒 Disconnected from → {self.dsn} as [{self.user.upper()}]")
        except oracledb.Error as e:
            print(f"❌ Disconnect error: {e}")
        finally:
            self._conn = None  # reset to None regardless

    def __enter__(self):
        """Auto-connect when used in 'with' block."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Auto-disconnect when 'with' block ends."""
        self.disconnect()

    def query(self, sql, params=None):
        """
        Run a SELECT query. Returns Pandas DataFrame.
        Must call connect() before this.

        Args:
            sql    (str) : SQL string with :param_name bind variables
            params (dict): Bind variable values

        Returns:
            pd.DataFrame or None if not connected
        """
        if self._conn is None:
            print("❌ Not connected. Call connect() first.")
            return None

        try:
            with self._conn.cursor() as cursor:
                cursor.execute(sql, params or {})
                cols = [col[0] for col in cursor.description]
                return pd.DataFrame(cursor.fetchall(), columns=cols)
        except oracledb.Error as e:
            print(f"❌ Query failed: {e}")
            return None

    def execute(self, sql, params=None, commit=True):
        """
        Run INSERT, UPDATE, or DELETE.
        Must call connect() before this.

        Args:
            sql    (str)  : SQL statement
            params (dict) : Bind variables
            commit (bool) : Auto-commit (default True)

        Returns:
            int: rows affected, or None if not connected
        """
        if self._conn is None:
            print("❌ Not connected. Call connect() first.")
            return None

        try:
            with self._conn.cursor() as cursor:
                cursor.execute(sql, params or {})
                if commit:
                    self._conn.commit()
                return cursor.rowcount
        except oracledb.Error as e:
            print(f"❌ Execute failed: {e}")
            return None

    def test(self):
        """Check connection by printing DB time and current user."""
        print("Testing----- connection to Oracle DB.")
        df = self.query("SELECT SYSDATE AS db_time, USER AS db_user FROM dual")
        if df is not None:
            print(df)
            print("Tested----- DB is connected successfully.")

    def is_connected(self):
        """Returns True if currently connected."""
        return self._conn is not None

if __name__ == "__main__":
    db = OracleDB()
    db.connect()
    db.test()
    db.disconnect()
