from dotenv import load_dotenv

load_dotenv()

from db.bigquery import create_table_if_not_exists


def main():
    create_table_if_not_exists()
    print("Hello from test-projekt!")


if __name__ == "__main__":
    main()
