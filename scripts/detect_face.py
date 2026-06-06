#!/usr/bin/env python3
"""
Detect the largest face in a video file and return its center as normalized (0–1) coordinates.
Samples multiple frames and picks the most common x position to avoid outliers.
Prints a single JSON line to stdout.
"""
import sys, json, statistics
import cv2

def detect_face(video_path: str) -> dict:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {"face_x": 0.5, "face_y": 0.35, "detected": False}

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    # Sample up to 8 evenly-spaced frames
    sample_positions = [int(total * f) for f in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85)]
    face_xs: list[float] = []
    face_ys: list[float] = []

    for pos in sample_positions:
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(pos, total - 1)))
        ret, frame = cap.read()
        if not ret:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
        if len(faces) == 0:
            continue
        # Use the largest detected face
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        face_xs.append((fx + fw / 2) / w)
        face_ys.append((fy + fh / 2) / h)

    cap.release()

    if not face_xs:
        return {"face_x": 0.5, "face_y": 0.35, "detected": False}

    # Use the median to reject outlier frames
    return {
        "face_x": statistics.median(face_xs),
        "face_y": statistics.median(face_ys),
        "detected": True,
        "samples": len(face_xs),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"face_x": 0.5, "face_y": 0.35, "detected": False, "error": "no path"}))
        sys.exit(0)
    result = detect_face(sys.argv[1])
    print(json.dumps(result))
