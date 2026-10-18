import pymysql
from constant import mysql_offline_config
from logging_config import get_logger

RETRY_TIMES = 2

class MySQLDB:
    def __init__(self, host, user, password, db, port=3306):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.db = db
        self.connection = None
        self.cursor = None
        self.logger = get_logger()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def connect(self):
        try:
            self.connection = pymysql.connect(host=self.host,
                                              port=self.port,
                                              user=self.user,
                                              password=self.password,
                                              database=self.db,
                                              charset='utf8mb4',
                                              cursorclass=pymysql.cursors.DictCursor)

            self.cursor = self.connection.cursor()
            return True
        except Exception as e:
            self.logger.error(f"The error ----{e}---- occurred")
        return False

    def check_and_reconnect(self):
        if not self.connection or not self.connection.open or not self.cursor:
            for attempt in range(RETRY_TIMES):
                self.logger.info(f"DB connection lost, trying to reconnect... Attempt {attempt+1}/{RETRY_TIMES}")
                if self.connect():
                    return True
            self.logger.error("Failed to reconnect after multiple attempts.")
            return False
        return True

    def close(self):
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
            self.logger.info("MySQL connection is closed")

    def execute_query(self, query, params=None):
        if not self.check_and_reconnect():
            return False, None
        for attempt in range(RETRY_TIMES):
            try:
                if params is None:
                    self.cursor.execute(query)
                else:
                    self.cursor.execute(query, params)
                self.connection.commit()
                return True, self.cursor.fetchall()
            except Exception as e:
                self.logger.error(f"The error --{e}-- occurred on attempt {attempt+1}/{RETRY_TIMES}")
                if attempt < RETRY_TIMES - 1:
                    self.check_and_reconnect()
        return False, None

    def select_data(self, table, where=None):
        if not self.check_and_reconnect():
            return False, None
        for attempt in range(RETRY_TIMES):
            try:
                query = f"SELECT * FROM {table}"
                if where:
                    query += f" WHERE {where}"
                return self.execute_query(query)
            except Exception as e:
                self.logger.error(f"Error while selecting data: {e} on attempt {attempt+1}/{RETRY_TIMES}")
                if attempt < RETRY_TIMES - 1:
                    self.check_and_reconnect()
        return False, None



# 使用示例
if __name__ == "__main__":
    db = MySQLDB(mysql_offline_config["host"], mysql_offline_config["username"], mysql_offline_config['password'],
                 mysql_offline_config['database'], mysql_offline_config["port"])
    db.connect()

    # 查询数据
    succ, results = db.select_data('product_capacity_maintenance')
    if succ:
        for row in results:
            print(row)
    else:
        print("查询失败")

    db.close()
