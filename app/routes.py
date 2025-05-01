import os
import json
import uuid
from pathlib import Path
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, current_app, send_from_directory,
    abort, jsonify
)
from werkzeug.utils import secure_filename
from PIL import Image
import pillow_heif
from moviepy.video.io.VideoFileClip import VideoFileClip
from flask_login import login_required, current_user, logout_user
from .models import db, User, SharedAccess

main = Blueprint("main", __name__)

# —— Configuration Constants —— #
BASE_DIR          = Path(__file__).parent.parent
UPLOAD_BASE       = BASE_DIR / 'uploads'
DATA_DIR          = BASE_DIR
DESCRIPTION_PATH  = DATA_DIR / "descriptions.json"
ALBUM_PATH        = DATA_DIR / "albums.json"
COMMENTS_PATH     = DATA_DIR / "comments.json"
TAGS_PATH         = DATA_DIR / "tags.json"
THUMB_FOLDER      = BASE_DIR / "app" / "static" / "thumbnails"
THUMB_SIZE_WIDTH  = 320

IMAGE_EXTENSIONS  = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "heic"}
VIDEO_EXTENSIONS  = {"mp4", "mov", "avi", "mkv"}
ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# ensure directories exist
UPLOAD_BASE.mkdir(parents=True, exist_ok=True)
THUMB_FOLDER.mkdir(parents=True, exist_ok=True)


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            current_app.logger.error(f"Invalid JSON in {path}")
    return {}


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


def generate_video_thumbnail(video_path: Path, thumb_path: Path) -> None:
    try:
        with VideoFileClip(str(video_path)) as clip:
            t = clip.duration / 2 if clip.duration > 1 else 0.1
            frame = clip.get_frame(t)
        img = Image.fromarray(frame)
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        new_h = int(h * (THUMB_SIZE_WIDTH / w))
        img = img.resize((THUMB_SIZE_WIDTH, new_h), Image.Resampling.LANCZOS)
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(thumb_path), format="JPEG")
    except Exception as e:
        current_app.logger.error(f"Error generating thumbnail for {video_path}: {e}")
        raise


def sanitize_filename(filename: str) -> str:
    clean = secure_filename(filename)
    if clean != filename:
        abort(400, "Invalid filename.")
    return filename


def user_folder(user_id: int) -> Path:
    """Get or create the upload folder for a given user."""
    path = UPLOAD_BASE / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


@main.route("/logout")
@login_required
def logout():
    """Log out the current user and redirect to login page."""
    logout_user()
    return redirect(url_for('auth.login'))


@main.route("/")
@login_required
def index():
    """Gallery view: shows current user's media plus galleries they've been shared."""
    descs    = load_json(DESCRIPTION_PATH)
    albums   = load_json(ALBUM_PATH)
    comments = load_json(COMMENTS_PATH)
    tags     = load_json(TAGS_PATH)

    tag_filter   = request.args.get("tag", "").strip().lower()
    search_query = request.args.get("search", "").strip().lower()

    UPLOAD_FOLDER = user_folder(current_user.id)
    media_files = sorted(
        [f for f in UPLOAD_FOLDER.iterdir() if f.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    images = []
    for path in media_files:
        fn  = path.name
        ext = path.suffix.lstrip('.').lower()
        file_tags = tags.get(fn, [])
        lt = [t.lower() for t in file_tags]

        if tag_filter and tag_filter not in lt:
            continue
        if search_query and not (
            search_query in fn.lower()
            or search_query in descs.get(fn, "").lower()
            or search_query in albums.get(fn, "").lower()
            or any(search_query in t.lower() for t in file_tags)
        ):
            continue

        media_type = "video" if ext in VIDEO_EXTENSIONS else "image"
        thumb = (
            url_for('main.thumbnail', filename=f"{path.stem}.jpg")
            if media_type == "video"
            else url_for('main.uploaded_file', filename=fn)
        )

        images.append({
            "filename": fn,
            "description": descs.get(fn, ""),
            "album": albums.get(fn, ""),
            "comments": comments.get(fn, []),
            "tags": file_tags,
            "type": media_type,
            "thumb": thumb,
        })

    all_tags = sorted({t for lst in tags.values() for t in lst}, key=str.lower)

    shared_accesses = SharedAccess.query.filter_by(
        shared_user_id=current_user.id
    ).all()

    favorites_map = load_json(ALBUM_PATH).get("favorites", {})
    user_favorites = favorites_map.get(str(current_user.id), [])

    return render_template(
        "gallery.html",
        images=images,
        descriptions=descs,
        albums=albums,
        tags=tags,
        all_tags=all_tags,
        current_tag=tag_filter,
        search_query=search_query,
        shared_accesses=shared_accesses,
        favorites=user_favorites
    )


@main.route("/albums")
@login_required
def albums():
    """Albums view: dynamically list albums with counts and preview thumbnails."""
    raw_data = load_json(ALBUM_PATH)
    albums_map = {k: v for k, v in raw_data.items() if k != "favorites"}

    UPLOAD_FOLDER = user_folder(current_user.id)
    media_files = sorted(
        [f for f in UPLOAD_FOLDER.iterdir() if f.is_file() and allowed_file(f.name)],
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    album_titles = sorted({v for v in albums_map.values()}, key=str.lower)
    grouped = {title: [] for title in album_titles}

    for f in media_files:
        fn = f.name
        title = albums_map.get(fn, "")
        if title in grouped:
            grouped[title].append(f)

    albums_data = []
    for title in album_titles:
        files = grouped.get(title, [])
        photos = sum(1 for f in files if f.suffix.lstrip('.').lower() in IMAGE_EXTENSIONS)
        videos = sum(1 for f in files if f.suffix.lstrip('.').lower() in VIDEO_EXTENSIONS)

        thumbnails = []
        for f in files[:5]:
            ext = f.suffix.lstrip('.').lower()
            if ext in VIDEO_EXTENSIONS:
                thumbnails.append(url_for('main.thumbnail', filename=f"{f.stem}.jpg"))
            else:
                thumbnails.append(url_for('main.uploaded_file', filename=f.name))

        albums_data.append({
            "title": title,
            "photos": photos,
            "videos": videos,
            "thumbnails": thumbnails
        })

    favorites_map = raw_data.get("favorites", {})
    user_favorites = favorites_map.get(str(current_user.id), [])

    return render_template("albums.html", albums_data=albums_data, favorites=user_favorites)


@main.route("/album/<album_title>")
@login_required
def view_album(album_title):
    """Show all media in a specific album for the current user."""
    descs    = load_json(DESCRIPTION_PATH)
    albums   = load_json(ALBUM_PATH)
    comments = load_json(COMMENTS_PATH)
    tags     = load_json(TAGS_PATH)

    UPLOAD_FOLDER = user_folder(current_user.id)
    media_files = sorted(
        [
            f for f in UPLOAD_FOLDER.iterdir()
            if f.is_file()
            and allowed_file(f.name)
            and albums.get(f.name) == album_title
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    media = []
    for path in media_files:
        fn  = path.name
        ext = path.suffix.lstrip('.').lower()
        media_type = "video" if ext in VIDEO_EXTENSIONS else "image"
        thumb = (
            url_for('main.thumbnail', filename=f"{path.stem}.jpg")
            if media_type == "video"
            else url_for('main.uploaded_file', filename=fn)
        )
        media.append({
            "filename": fn,
            "description": descs.get(fn, ""),
            "tags": tags.get(fn, []),
            "comments": comments.get(fn, []),
            "type": media_type,
            "thumb": thumb
        })

    return render_template(
        "album_view.html",
        album_title=album_title,
        media_items=media
    )


@main.route("/create_album", methods=["POST"])
@login_required
def create_album():
    """Create a new empty album entry, preserving favorites."""
    name = request.form.get("title", "").strip()
    if not name:
        flash("Album name cannot be empty.", "error")
    elif len(name) > 50:
        flash("Album name must be 50 characters or fewer.", "error")
    else:
        data = load_json(ALBUM_PATH)
        favorites = data.get("favorites")
        albums_map = {k: v for k, v in data.items() if k != "favorites"}
        existing = set(albums_map.values())

        if name in existing:
            flash(f"Album '{name}' already exists.", "info")
        else:
            albums_map[name] = name
            new_data = dict(albums_map)
            if favorites is not None:
                new_data["favorites"] = favorites
            save_json(ALBUM_PATH, new_data)
            flash(f"Album '{name}' created.", "success")

    return redirect(url_for("main.albums"))


@main.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    """Upload handler: shared or owner uploads with alias tagging,
       plus album selection and new-album creation support."""
    shared_accesses = SharedAccess.query.filter_by(
        shared_user_id=current_user.id
    ).all()
    albums_map = load_json(ALBUM_PATH)
    album_titles = sorted({v for k, v in albums_map.items() if k != "favorites"}, key=str.lower)

    if request.method == "POST":
        owner_id = int(request.form.get("owner_id", current_user.id))
        if owner_id != current_user.id:
            access = SharedAccess.query.filter_by(
                owner_id=owner_id,
                shared_user_id=current_user.id
            ).first()
            if not access or not access.can_upload:
                abort(403)
            uploader_alias = access.alias
        else:
            uploader_alias = current_user.username

        files = request.files.getlist("photos")
        album = request.form.get("album", "").strip()
        new_album = request.form.get("new_album", "").strip()
        chosen_album = new_album or album

        descs    = load_json(DESCRIPTION_PATH)
        albums   = load_json(ALBUM_PATH)
        comments = load_json(COMMENTS_PATH)
        tags     = load_json(TAGS_PATH)

        UPLOAD_FOLDER = user_folder(owner_id)

        for file in files:
            if file and allowed_file(file.filename):
                orig = secure_filename(file.filename)
                ext  = orig.rsplit('.', 1)[1].lower()
                filename = f"{uuid.uuid4().hex}.{ext}"
                save_path = UPLOAD_FOLDER / filename

                if ext == 'heic':
                    try:
                        hf = pillow_heif.read_heif(file.stream)
                        img = Image.frombytes(hf.mode, hf.size, hf.data, 'raw')
                        filename = f"{uuid.uuid4().hex}.jpg"
                        save_path = UPLOAD_FOLDER / filename
                        img.save(str(save_path), format='JPEG')
                        flash('HEIC converted and uploaded.', 'success')
                    except Exception as e:
                        flash(f'HEIC conversion failed: {e}', 'error')
                        continue
                else:
                    file.save(str(save_path))

                if ext in VIDEO_EXTENSIONS:
                    thumb_file = THUMB_FOLDER / f"{save_path.stem}.jpg"
                    try:
                        generate_video_thumbnail(save_path, thumb_file)
                    except Exception as e:
                        flash(f"Thumbnail failed for {filename}: {e}", 'error')

                if chosen_album:
                    albums[filename] = chosen_album
                descs.setdefault(filename, "")
                comments.setdefault(filename, [])
                tags.setdefault(filename, [])

                comments[filename].insert(0, f"Uploaded by {uploader_alias}")

        save_json(DESCRIPTION_PATH, descs)
        save_json(ALBUM_PATH, albums)
        save_json(COMMENTS_PATH, comments)
        save_json(TAGS_PATH, tags)

        flash('Upload successful.', 'success')
        return redirect(url_for('main.index'))

    return render_template(
        'upload.html',
        shared_accesses=shared_accesses,
        album_titles=album_titles
    )


@main.route("/add_tag/<filename>", methods=["POST"])
@login_required
def add_tag(filename):
    fn = sanitize_filename(filename)
    tags = load_json(TAGS_PATH)
    new = request.form.get("tag", "").strip()
    if new:
        tags.setdefault(fn, [])
        if new not in tags[fn]:
            tags[fn].append(new)
            save_json(TAGS_PATH, tags)
            flash(f"Tag '{new}' added.", 'success')
        else:
            flash(f"Tag '{new}' exists.", 'info')
    else:
        flash("Empty tag not added.", 'warning')
    return redirect(url_for('main.index'))


@main.route("/remove_tag/<filename>/<tag>", methods=["POST"])
@login_required
def remove_tag(filename, tag):
    fn = sanitize_filename(filename)
    tags = load_json(TAGS_PATH)
    tags.setdefault(fn, [])
    tags[fn] = [t for t in tags[fn] if t.lower() != tag.lower()]
    save_json(TAGS_PATH, tags)
    flash(f"Tag '{tag}' removed.", 'success')
    return redirect(url_for('main.index'))


@main.route("/rename_tag_single", methods=["POST"])
@login_required
def rename_tag_single():
    fn = sanitize_filename(request.form.get("filename", ""))
    old = request.form.get("old_tag", "").strip().lower()
    new = request.form.get("new_tag", "").strip()
    if not old or not new:
        flash("Missing rename data.", 'warning')
        return redirect(url_for('main.index'))

    tags = load_json(TAGS_PATH)
    tags.setdefault(fn, [])
    tags[fn] = [new if t.lower() == old else t for t in tags[fn]]
    save_json(TAGS_PATH, tags)
    flash(f"Renamed '{old}'→'{new}' on {fn}.", 'success')
    return redirect(url_for('main.index'))


@main.route("/rename_tag_global", methods=["POST"])
@login_required
def rename_tag_global():
    old = request.form.get("old_tag", "").strip().lower()
    new = request.form.get("new_tag", "").strip()
    if not old or not new:
        flash("Missing rename data.", 'warning')
        return redirect(url_for('main.index'))

    tags = load_json(TAGS_PATH)
    updated = False
    for fn, lst in tags.items():
        new_lst = [new if t.lower() == old else t for t in lst]
        if new_lst != lst:
            tags[fn] = new_lst
            updated = True

    if updated:
        save_json(TAGS_PATH, tags)
        flash(f"Renamed '{old}'→'{new}' globally.", 'success')
    else:
        flash(f"No matches for '{old}'.", 'info')
    return redirect(url_for('main.index'))


@main.route("/tag/<tagname>")
@login_required
def filter_by_tag(tagname):
    return redirect(url_for('main.index', tag=tagname))


@main.route("/delete/<filename>", methods=["POST"])
@login_required
def delete_image(filename):
    fn = sanitize_filename(filename)
    UPLOAD_FOLDER = user_folder(current_user.id)
    fp = UPLOAD_FOLDER / fn
    tp = THUMB_FOLDER / f"{Path(fn).stem}.jpg"

    descs    = load_json(DESCRIPTION_PATH)
    albums   = load_json(ALBUM_PATH)
    comments = load_json(COMMENTS_PATH)
    tags     = load_json(TAGS_PATH)

    if fp.exists():
        fp.unlink()
        descs.pop(fn, None)
        albums.pop(fn, None)
        comments.pop(fn, None)
        tags.pop(fn, None)
        if tp.exists():
            tp.unlink()
        flash(f"{fn} deleted.", 'success')
    else:
        flash(f"{fn} not found.", 'error')

    save_json(DESCRIPTION_PATH, descs)
    save_json(ALBUM_PATH, albums)
    save_json(COMMENTS_PATH, comments)
    save_json(TAGS_PATH, tags)
    return redirect(url_for('main.index'))


@main.route("/download/<filename>")
@login_required
def download_image(filename):
    fn = sanitize_filename(filename)
    UPLOAD_FOLDER = user_folder(current_user.id)
    return send_from_directory(str(UPLOAD_FOLDER), fn, as_attachment=True)


@main.route("/update_description/<filename>", methods=["POST"])
@login_required
def update_description(filename):
    fn = sanitize_filename(filename)
    descs = load_json(DESCRIPTION_PATH)
    descs[fn] = request.form.get("description", "").strip()
    save_json(DESCRIPTION_PATH, descs)
    flash("Description updated.", 'success')
    return redirect(url_for('main.index'))


@main.route("/add_comment/<filename>", methods=["POST"])
@login_required
def add_comment(filename):
    fn = sanitize_filename(filename)
    comment_text = request.form.get("comment", "").strip()
    if not comment_text:
        flash("Empty comment not added.", 'warning')
        return redirect(url_for('main.index'))

    owner_id = int(request.form.get("owner_id", current_user.id))
    if owner_id != current_user.id:
        access = SharedAccess.query.filter_by(
            owner_id=owner_id,
            shared_user_id=current_user.id
        ).first()
        if not access or not access.can_comment:
            abort(403)
        commenter_alias = access.alias
    else:
        commenter_alias = current_user.username

    comments = load_json(COMMENTS_PATH)
    comments.setdefault(fn, [])
    comments[fn].append(f"{commenter_alias} — {comment_text}")
    save_json(COMMENTS_PATH, comments)

    flash("Comment added.", 'success')
    return redirect(url_for('main.index'))


@main.route("/share", methods=["POST"])
@login_required
def share():
    primary_id = current_user.id
    shared_username = request.form.get("username")
    alias = request.form.get("alias", "").strip() or None

    user = User.query.filter_by(username=shared_username).first()
    if not user:
        flash('User not found', 'error')
        return redirect(url_for('main.index'))

    existing = SharedAccess.query.filter_by(
        owner_id=primary_id,
        shared_user_id=user.id
    ).first()
    if existing:
        flash('Access already granted', 'info')
        return redirect(url_for('main.index'))

    access = SharedAccess(
        owner_id=primary_id,
        shared_user_id=user.id,
        alias=alias or user.username,
        can_upload=True,
        can_comment=True
    )
    db.session.add(access)
    db.session.commit()

    flash(f"Granted gallery access to {user.username}", 'success')
    return redirect(url_for('main.index'))


@main.route("/uploads/<filename>")
@login_required
def uploaded_file(filename):
    fn = sanitize_filename(filename)
    user_dir = user_folder(current_user.id)
    return send_from_directory(str(user_dir), fn)


@main.route("/thumbnails/<filename>")
@login_required
def thumbnail(filename):
    fn = sanitize_filename(filename)
    return send_from_directory(str(THUMB_FOLDER), fn)


@main.route("/toggle_favorite_album", methods=["POST"])
@login_required
def toggle_favorite_album():
    """Toggle favorite status for an album for the current user."""
    album = request.form.get("album", "").strip()
    if not album:
        return jsonify({"status": "error", "message": "No album specified"}), 400

    data = load_json(ALBUM_PATH)
    favorites = data.get("favorites", {})
    user_id_str = str(current_user.id)
    user_favs = favorites.get(user_id_str, [])

    if album in user_favs:
        user_favs.remove(album)
        action = "unfavorited"
    else:
        user_favs.append(album)
        action = "favorited"

    favorites[user_id_str] = user_favs
    data["favorites"] = favorites
    save_json(ALBUM_PATH, data)

    return jsonify({"status": "success", "action": action})
