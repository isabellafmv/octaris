import cv2
import numpy as np
import time
from dataclasses import dataclass


@dataclass
class CalibrationResult:
    homography: np.ndarray  # 3x3 pixel-to-mm transform
    center_px: tuple[int, int]  # center of the 4 screws in pixel coords

    def px_to_mm(self, px_x: int, px_y: int) -> tuple[float, float]:
        """Convert a pixel coordinate to mm relative to platform center."""
        pt = np.array([px_x, px_y, 1.0])
        mapped = self.homography @ pt
        mapped /= mapped[2]
        return (float(mapped[0]), float(mapped[1]))


def _find_screw_centers(frame, threshold=180, min_area=80, max_area=5000):
    """Find white screw head centers using binary threshold on grayscale."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (11, 11), 0)
    _, thresh = cv2.threshold(blurred, threshold, 255, cv2.THRESH_BINARY)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    centers = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area or area > max_area:
            continue
        perimeter = cv2.arcLength(c, True)
        if perimeter == 0:
            continue
        circularity = 4 * np.pi * area / (perimeter * perimeter)
        if circularity < 0.5:
            continue
        M = cv2.moments(c)
        if M["m00"] == 0:
            continue
        cx = int(M["m10"] / M["m00"])
        cy = int(M["m01"] / M["m00"])
        centers.append((cx, cy))

    return centers


def _sort_grid(points):
    """Sort 4 points into [top-left, top-right, bottom-left, bottom-right]."""
    pts = np.array(points, dtype=np.float32)
    centroid = pts.mean(axis=0)

    tl = [p for p in points if p[0] < centroid[0] and p[1] < centroid[1]]
    tr = [p for p in points if p[0] >= centroid[0] and p[1] < centroid[1]]
    bl = [p for p in points if p[0] < centroid[0] and p[1] >= centroid[1]]
    br = [p for p in points if p[0] >= centroid[0] and p[1] >= centroid[1]]

    if not (len(tl) == 1 and len(tr) == 1 and len(bl) == 1 and len(br) == 1):
        return None
    return [tl[0], tr[0], bl[0], br[0]]


def auto_calibrate(cap, screw_spacing_mm=20, threshold=180, duration=3) -> CalibrationResult | None:
    """Detect 4 screw heads and compute a perspective transform (pixel -> mm).

    The 4 screws form a square grid with `screw_spacing_mm` between
    adjacent screws. The center of the 4 screws is defined as (0, 0) mm.

    Uses cv2.getPerspectiveTransform to handle camera angle distortion,
    so both X and Y are accurate regardless of perspective.

    Returns CalibrationResult or None if detection failed.
    """
    half = screw_spacing_mm / 2.0
    # Real-world coordinates: TL, TR, BL, BR relative to center
    world_pts = np.array([
        [-half, -half],
        [half, -half],
        [-half, half],
        [half, half],
    ], dtype=np.float32)

    homography_samples = []
    center_samples = []
    start = time.time()

    while time.time() - start < duration:
        ret, frame = cap.read()
        if not ret:
            continue

        centers = _find_screw_centers(frame, threshold=threshold)
        if len(centers) != 4:
            cv2.imshow("Calibration", frame)
            cv2.waitKey(1)
            continue

        grid = _sort_grid(centers)
        if grid is None:
            cv2.imshow("Calibration", frame)
            cv2.waitKey(1)
            continue

        pixel_pts = np.array(grid, dtype=np.float32)
        H = cv2.getPerspectiveTransform(pixel_pts, world_pts)
        homography_samples.append(H)

        cx = int(np.mean([p[0] for p in grid]))
        cy = int(np.mean([p[1] for p in grid]))
        center_samples.append((cx, cy))

        # draw feedback with labels
        labels = ["TL", "TR", "BL", "BR"]
        for p, lbl in zip(grid, labels):
            cv2.circle(frame, p, 6, (0, 255, 0), 2)
            cv2.putText(frame, lbl, (p[0]+8, p[1]-8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        cv2.circle(frame, (cx, cy), 4, (0, 0, 255), -1)
        cv2.putText(frame, f"Detected 4 screws ({len(homography_samples)} samples)",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.imshow("Calibration", frame)
        cv2.waitKey(1)

    if not homography_samples:
        return None

    # Average the homography matrices
    H_avg = np.median(np.stack(homography_samples), axis=0)

    return CalibrationResult(
        homography=H_avg,
        center_px=(
            int(np.median([c[0] for c in center_samples])),
            int(np.median([c[1] for c in center_samples])),
        ),
    )


def detect_offset(cap, cal: CalibrationResult, duration=5) -> tuple[float, float] | None:
    """Detect the nozzle-to-center offset in mm.

    Finds the nozzle tip via HSV and uses the homography to convert
    the pixel position to mm coordinates (where platform center = 0,0).

    Returns (dx_mm, dy_mm) or None if detection failed.
    """
    offsets = []
    start = time.time()

    while time.time() - start < duration:
        ret, frame = cap.read()
        if not ret:
            continue

        # nozzle detection via HSV
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([170, 130, 110]), np.array([176, 190, 225]))
        # mask = cv2.inRange(hsv, np.array([70, 120, 140]), np.array([80, 175, 180]))

        # [70, 120, 140]
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        nozzle = None
        if contours:
            largest = max(contours, key=cv2.contourArea)
            if cv2.contourArea(largest) > 100:
                M = cv2.moments(largest)
                if M["m00"] > 0:
                    nozzle = (int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"]))

        if nozzle:
            dx_mm, dy_mm = cal.px_to_mm(nozzle[0], nozzle[1])
            offsets.append((dx_mm, dy_mm))

            # draw feedback
            cv2.circle(frame, nozzle, 5, (0, 255, 255), -1)
            cv2.circle(frame, cal.center_px, 5, (0, 0, 255), -1)
            cv2.line(frame, nozzle, cal.center_px, (255, 0, 255), 1)
            cv2.putText(frame, f"Offset: ({dx_mm:.2f}, {dy_mm:.2f}) mm",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        cv2.imshow("Detection", frame)
        cv2.waitKey(1)

    if not offsets:
        return None

    return (
        float(np.median([o[0] for o in offsets])),
        float(np.median([o[1] for o in offsets])),
    )


def inspect_hsv(cap):
    """Live viewer — click any pixel to print its HSV value. Press q to quit."""
    hsv_frame = [None]

    def on_click(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and hsv_frame[0] is not None:
            print(f"HSV at ({x},{y}): {hsv_frame[0][y, x]}")

    cv2.namedWindow("HSV Inspector")
    cv2.setMouseCallback("HSV Inspector", on_click)

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        hsv_frame[0] = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        cv2.imshow("HSV Inspector", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cv2.destroyWindow("HSV Inspector")


if __name__ == "__main__":
    cap = cv2.VideoCapture(1)
    if not cap.isOpened():
        print("Error: Could not open camera.")
        exit(1)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    print("Press q in HSV Inspector to continue to calibration...")
    inspect_hsv(cap)

    print("Calibrating with 4 screw heads (20mm spacing)...")
    cal = auto_calibrate(cap, screw_spacing_mm=20)

    if cal is None:
        print("Calibration failed — could not detect 4 screw heads.")
        print("Tips: adjust threshold, check lighting, ensure all 4 screws visible.")
        cap.release()
        cv2.destroyAllWindows()
        exit(1)

    print(f"Platform center: {cal.center_px}")
    # Verify by mapping each screw — should read ±10mm
    print("Verification (should be ±10mm at each screw):")

    print("\nDetecting nozzle offset...")
    offset = detect_offset(cap, cal)

    if offset is None:
        print("Could not detect nozzle.")
    else:
        print(f"Nozzle offset from center: ({offset[0]:.2f}, {offset[1]:.2f}) mm")

    cap.release()
    cv2.destroyAllWindows()
