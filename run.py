from app import create_app
import os

app = create_app()

if __name__ == '__main__':
    # app.run(debug=True)
    host = '0.0.0.0'  # Listen on all network interfaces
    port = int(5000)
    if os.name == "nt":
        app.run(host=host, debug=True)
    else:
        app.run(host=host, port=port)
