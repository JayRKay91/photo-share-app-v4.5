from app import create_app, db

app = create_app()

# --- Ensure application context before accessing the database ---
with app.app_context():
    from app.models import User, SharedAccess, Photo, Comment  # <-- your models
    db.create_all()
    print("✅ Database checked/created successfully.")

if __name__ == "__main__":
    # Match v3 behavior (host="0.0.0.0", debug=True) while preserving v4 setup
    app.run(host="0.0.0.0", debug=True)
