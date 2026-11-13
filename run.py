from app import create_app
import os

app = create_app()

if __name__ == '__main__':
    # app.run(debug=True)
    host = 'localhost'  # Listen on all network interfaces
    port = int(5000)
    if os.name == "nt":
        app.run(debug=True)
    else:
        app.run(host=host, port=port)
