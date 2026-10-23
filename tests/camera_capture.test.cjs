const assert = require("node:assert/strict");
const {test} = require("node:test");

const {openCameraStream} = require("../static/js/camera_capture.js");

test("permission denial does not retry camera access", async () => {
  const calls = [];
  const denied = Object.assign(new Error("permission denied"), {name: "NotAllowedError"});

  await assert.rejects(openCameraStream({
    async getUserMedia(constraints) {
      calls.push(constraints);
      throw denied;
    },
  }), {name: "NotAllowedError"});

  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], {video: {facingMode: {exact: "environment"}}, audio: false});
});

test("unsupported rear-camera constraints retry with the default camera", async () => {
  const calls = [];
  const stream = {getTracks: () => []};

  const result = await openCameraStream({
    async getUserMedia(constraints) {
      calls.push(constraints);
      if (calls.length === 1) {
        throw Object.assign(new Error("rear camera unavailable"), {name: "OverconstrainedError"});
      }
      return stream;
    },
  });

  assert.equal(result, stream);
  assert.deepEqual(calls, [
    {video: {facingMode: {exact: "environment"}}, audio: false},
    {video: true, audio: false},
  ]);
});

test("a stream returned after cancellation has every track stopped", async () => {
  let completeRequest;
  let current = true;
  let stopped = 0;
  const pending = openCameraStream({
    getUserMedia: () => new Promise(resolve => { completeRequest = resolve; }),
  }, () => current);

  current = false;
  completeRequest({getTracks: () => [{stop: () => { stopped += 1; }}]});

  assert.equal(await pending, null);
  assert.equal(stopped, 1);
});

test("missing camera API fails explicitly", async () => {
  await assert.rejects(openCameraStream(null), {name: "NotSupportedError"});
});
