(() => {
  "use strict";

  const FALLBACK_CONSTRAINT_ERRORS = new Set(["OverconstrainedError", "TypeError"]);

  async function openCameraStream(mediaDevices, isCurrent = () => true) {
    if (!mediaDevices || typeof mediaDevices.getUserMedia !== "function") {
      const error = new Error("Camera capture is not supported.");
      error.name = "NotSupportedError";
      throw error;
    }

    let stream;
    try {
      stream = await mediaDevices.getUserMedia({
        video: {facingMode: {exact: "environment"}},
        audio: false,
      });
    } catch (error) {
      if (!FALLBACK_CONSTRAINT_ERRORS.has(error?.name)) throw error;
      stream = await mediaDevices.getUserMedia({video: true, audio: false});
    }

    if (!isCurrent()) {
      stream.getTracks().forEach(track => track.stop());
      return null;
    }
    return stream;
  }

  const cameraCapture = {openCameraStream};
  if (typeof module !== "undefined" && module.exports) module.exports = cameraCapture;
  if (typeof window !== "undefined") window.SimpleOfficeCamera = cameraCapture;
})();
