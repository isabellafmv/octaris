import cv2 as cv
import numpy as np


# def get_cameras():
#     cameras = []
#     for i in range(5):
#         cap = cv.VideoCapture(i)
#         if cap.isOpen() == True:
#             cameras.append(i)
#     return cameras

cap = cv.VideoCapture(1)

if not cap.isOpened():
    print("Error: Could not open camera.")
    exit()

cap.set(cv.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv.CAP_PROP_FRAME_HEIGHT, 480)

while True:
    ret, frame = cap.read()
    if not ret:
        print("Error: Could not read frame.")
        break

    cv.imshow('Camera', frame)
    if cv.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv.destroyAllWindows()
