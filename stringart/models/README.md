# Bundled models

`face_detection_yunet_2023mar.onnx` (227 KB) is YuNet, the face detector from the OpenCV model zoo
(https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet), MIT licensed by
Shiqi Yu; the licence text is in `LICENSE-yunet`. It runs through `cv2.FaceDetectorYN` on the CPU
in a few milliseconds and never leaves the server. It frames portraits and raises the importance of
faces and their features for the optimiser.
