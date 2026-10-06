from app import create_app

app = create_app()

if __name__ == '__main__':
    # Debug va abilitato esplicitamente con flask run --debug.
    app.run()
