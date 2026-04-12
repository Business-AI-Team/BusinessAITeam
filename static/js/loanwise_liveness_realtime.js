/**
 * Liveness coach: MediaPipe Tasks Vision — FaceLandmarker (browser ES module).
 * Replaces legacy face_mesh.js which often did not expose a working global FaceMesh.
 */
(function (w) {
  'use strict';

  var TASKS_VISION_VERSION = '0.10.14';
  var TASKS_ESM = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@' + TASKS_VISION_VERSION + '/+esm';
  var WASM_BASE = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@' + TASKS_VISION_VERSION + '/wasm';
  var MODEL_URL =
    'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task';

  var LEFT_EYE = [33, 160, 158, 133, 153, 144];
  var RIGHT_EYE = [362, 385, 387, 263, 373, 380];
  var EAR_THRESHOLD = 0.2;
  var BLINK_MIN_CLOSED_FRAMES = 2;
  var DONE_NOTIFY_DELAY_MS = 700;

  function yawFromLm(lm) {
    var nose = lm[1];
    var le = lm[33];
    var re = lm[263];
    var cx = (le.x + re.x) / 2;
    return (nose.x - cx) * 2;
  }

  function dist2d(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  function eyeAspectRatio(lm, indices, vw, vh) {
    var pts = indices.map(function (i) {
      return { x: lm[i].x * vw, y: lm[i].y * vh };
    });
    var A = dist2d(pts[1], pts[5]);
    var B = dist2d(pts[2], pts[4]);
    var C = dist2d(pts[0], pts[3]);
    return (A + B) / (2 * C);
  }

  function processFrame(lm, videoEl, options, state) {
    var msgs = options.messages || {};
    function tk(key) {
      return msgs[key] || key;
    }
    var vw = videoEl.videoWidth || 640;
    var vh = videoEl.videoHeight || 480;
    var y = yawFromLm(lm);
    var leftEar = eyeAspectRatio(lm, LEFT_EYE, vw, vh);
    var rightEar = eyeAspectRatio(lm, RIGHT_EYE, vw, vh);
    var avgEar = (leftEar + rightEar) / 2;

    if (state.step === 'face') {
      if (options.onHint) options.onHint(tk('show_face'));
      if (Math.abs(y) < 0.09) state.stableFront += 1;
      else state.stableFront = Math.max(0, state.stableFront - 2);
      if (state.stableFront >= 10) {
        state.step = 'left';
        state.stableFront = 0;
        if (options.onStep) options.onStep('left');
      }
    } else if (state.step === 'left') {
      if (options.onHint) options.onHint(tk('turn_left'));
      if (y > 0.09) state.stableLeft += 1;
      else state.stableLeft = Math.max(0, state.stableLeft - 1);
      if (state.stableLeft >= 8) {
        state.step = 'right';
        state.stableLeft = 0;
        if (options.onStep) options.onStep('right');
      }
    } else if (state.step === 'right') {
      if (options.onHint) options.onHint(tk('turn_right'));
      if (y < -0.09) state.stableRight += 1;
      else state.stableRight = Math.max(0, state.stableRight - 1);
      if (state.stableRight >= 8) {
        state.step = 'blink';
        state.blinkCounter = 0;
        state.blinkDone = false;
        if (options.onStep) options.onStep('blink');
      }
    } else if (state.step === 'blink') {
      if (options.onHint) options.onHint(tk('blink'));
      if (avgEar < EAR_THRESHOLD) {
        state.blinkCounter += 1;
      } else {
        if (state.blinkCounter >= BLINK_MIN_CLOSED_FRAMES) state.blinkDone = true;
        state.blinkCounter = 0;
      }
      if (state.blinkDone) {
        state.sequenceFinished = true;
        state.step = 'done';
        if (options.onHint) options.onHint(tk('done'));
        if (options.onStep) options.onStep('done');
        if (!state.completeCallbackFired && typeof options.onSequenceComplete === 'function') {
          state.completeCallbackFired = true;
          w.setTimeout(function () {
            try {
              options.onSequenceComplete();
            } catch (e) {
              console.warn('onSequenceComplete', e);
            }
          }, DONE_NOTIFY_DELAY_MS);
        }
        return true;
      }
    }
    return false;
  }

  w.LoanWiseLivenessRT = {
    create: function (videoEl, options) {
      options = options || {};
      var msgs = options.messages || {};
      function t(key) {
        return msgs[key] || key;
      }

      var landmarker = null;
      var rafId = null;
      var running = false;
      var lastVideoTime = -1;
      var state = {
        step: 'face',
        stableFront: 0,
        stableLeft: 0,
        stableRight: 0,
        blinkCounter: 0,
        blinkDone: false,
        sequenceFinished: false,
        completeCallbackFired: false,
      };

      function cleanup() {
        running = false;
        if (rafId) {
          w.cancelAnimationFrame(rafId);
          rafId = null;
        }
        if (landmarker && typeof landmarker.close === 'function') {
          landmarker.close();
        }
        landmarker = null;
        lastVideoTime = -1;
      }

      function onFrame() {
        if (!running || !landmarker) return;
        if (videoEl.readyState < 2) {
          rafId = w.requestAnimationFrame(onFrame);
          return;
        }
        if (state.sequenceFinished) return;

        var vt = videoEl.currentTime;
        if (vt === lastVideoTime) {
          rafId = w.requestAnimationFrame(onFrame);
          return;
        }
        lastVideoTime = vt;

        var result;
        try {
          result = landmarker.detectForVideo(videoEl, performance.now());
        } catch (e) {
          console.warn('detectForVideo', e);
          rafId = w.requestAnimationFrame(onFrame);
          return;
        }

        if (!result.faceLandmarks || !result.faceLandmarks[0]) {
          if (options.onHint) options.onHint(t('no_face'));
          rafId = w.requestAnimationFrame(onFrame);
          return;
        }

        var lm = result.faceLandmarks[0];
        var done = processFrame(lm, videoEl, options, state);
        if (done) {
          cleanup();
          return;
        }
        rafId = w.requestAnimationFrame(onFrame);
      }

      return {
        /**
         * Loads @mediapipe/tasks-vision (FaceLandmarker) from CDN, then starts the analysis loop.
         */
        start: function () {
          if (options.onHint) options.onHint(t('loading_model'));
          return import(/* webpackIgnore: true */ TASKS_ESM)
            .then(function (mod) {
              var FaceLandmarker = mod.FaceLandmarker;
              var FilesetResolver = mod.FilesetResolver;
              if (!FaceLandmarker || !FilesetResolver) {
                throw new Error('tasks-vision exports missing');
              }
              return FilesetResolver.forVisionTasks(WASM_BASE).then(function (resolver) {
                return FaceLandmarker.createFromOptions(resolver, {
                  baseOptions: {
                    modelAssetPath: MODEL_URL,
                    delegate: 'CPU',
                  },
                  runningMode: 'VIDEO',
                  numFaces: 1,
                  minFaceDetectionConfidence: 0.5,
                  minFacePresenceConfidence: 0.5,
                  minTrackingConfidence: 0.5,
                  outputFaceBlendshapes: false,
                  outputFacialTransformationMatrixes: false,
                });
              });
            })
            .then(function (fl) {
              landmarker = fl;
              state.sequenceFinished = false;
              state.completeCallbackFired = false;
              state.step = 'face';
              state.stableFront = state.stableLeft = state.stableRight = 0;
              state.blinkCounter = 0;
              state.blinkDone = false;
              lastVideoTime = -1;
              if (options.onHint) options.onHint(t('show_face'));
              running = true;
              rafId = w.requestAnimationFrame(onFrame);
            })
            .catch(function (err) {
              console.error('LoanWiseLivenessRT start', err);
              if (options.onHint) options.onHint(t('no_lib'));
              return Promise.reject(err);
            });
        },
        stop: function () {
          state.sequenceFinished = true;
          cleanup();
          state.step = 'face';
          state.stableFront = state.stableLeft = state.stableRight = 0;
          state.blinkCounter = 0;
          state.blinkDone = false;
          state.completeCallbackFired = false;
        },
        getStep: function () {
          return state.step;
        },
        isComplete: function () {
          return state.step === 'done';
        },
      };
    },
  };
})(window);
