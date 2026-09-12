"""Exercise the rewarded-ad lifecycle without loading the real ad provider."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REWARDED_ADS = ROOT / "frontend" / "rewarded-ads.js"
NODE = shutil.which("node")


NODE_HARNESS = r"""
const fs = require("node:fs");
const vm = require("node:vm");
const scenario = process.argv[1];
const source = fs.readFileSync(process.argv[2], "utf8");

let now = 0;
let nextTimer = 1;
const timers = new Map();
const setTimeoutFake = (callback, delay = 0) => {
  const id = nextTimer++;
  timers.set(id, {callback, due: now + Math.max(0, Number(delay) || 0)});
  return id;
};
const clearTimeoutFake = id => timers.delete(id);
const flush = () => new Promise(resolve => setImmediate(resolve));
const advance = async milliseconds => {
  const target = now + milliseconds;
  while (true) {
    let selected = null;
    for (const [id, timer] of timers) {
      if (timer.due > target) continue;
      if (!selected || timer.due < selected.timer.due) selected = {id, timer};
    }
    if (!selected) break;
    now = selected.timer.due;
    timers.delete(selected.id);
    selected.timer.callback();
    await flush();
  }
  now = target;
  await flush();
};

const listeners = new Map();
const calls = [];
let providerScript = null;
const slot = {
  addService(service) {
    calls.push(["add-service", service === pubads]);
    return this;
  },
};
const pubads = {
  addEventListener(name, callback) {
    const registered = listeners.get(name) || new Set();
    registered.add(callback);
    listeners.set(name, registered);
  },
  removeEventListener(name, callback) {
    listeners.get(name)?.delete(callback);
  },
  emit(name, event = {}) {
    for (const callback of [...(listeners.get(name) || [])]) callback({slot, ...event});
  },
};
const googletag = {
  apiReady: true,
  enums: {OutOfPageFormat: {REWARDED: "rewarded"}},
  cmd: {push(callback) { callback(); return 1; }},
  defineOutOfPageSlot(path, format) {
    if (scenario === "provider_start_failure") {
      throw new Error("synthetic-provider-start-failure");
    }
    calls.push(["define", path, format]);
    return slot;
  },
  pubads: () => pubads,
  enableServices() { calls.push(["enable"]); },
  display(candidate) { calls.push(["display", candidate === slot]); },
  destroySlots(slots) {
    calls.push(["destroy", slots.length, slots[0] === slot]);
    return true;
  },
};

const context = {
  console,
  Date: {now: () => now},
  document: {
    createElement: () => ({}),
    head: {append(script) { providerScript = script; }},
  },
  LectureSiftConsent: {allows: category => category === "advertising"},
  setTimeout: setTimeoutFake,
  clearTimeout: clearTimeoutFake,
};
context.window = context;
if (scenario !== "provider_failure") context.googletag = googletag;
vm.runInNewContext(source, context, {filename: "rewarded-ads.js"});

const outcome = promise => promise.then(
  value => ({status: "fulfilled", value}),
  error => ({status: "rejected", error: error?.message || String(error)}),
);

async function runLifecycle() {
  const order = [];
  let releasePresentedCheckpoint;
  let releasePresented;
  let releaseGranted;
  const presentedCheckpoint = new Promise(resolve => { releasePresentedCheckpoint = resolve; });
  const presentedResponse = new Promise(resolve => { releasePresented = resolve; });
  const grantedResponse = new Promise(resolve => { releaseGranted = resolve; });
  const shown = context.LectureSiftRewardedAds.show("/123/rewarded", {
    onPresented: async () => {
      order.push("presented:start");
      await presentedCheckpoint;
      order.push("presented:pending");
      await presentedResponse;
      order.push("presented:end");
    },
    onGranted: async () => {
      order.push("granted:start");
      await grantedResponse;
      order.push("granted:end");
    },
    timeoutMs: 15_000,
  });
  const claim = shown.then(value => {
    order.push("claim");
    return value;
  });

  await flush();
  pubads.emit("rewardedSlotReady", {makeRewardedVisible: () => true});
  await flush();
  const afterReady = [...order];

  releasePresentedCheckpoint();
  await flush();
  const beforeProviderGrant = [...order];

  pubads.emit("rewardedSlotGranted");
  await flush();
  const whilePresentedPending = [...order];

  releasePresented();
  await flush();
  const whileGrantPending = [...order];

  releaseGranted();
  const value = await claim;
  const destroyedBeforeClose = calls.filter(call => call[0] === "destroy").length;
  pubads.emit("rewardedSlotClosed");
  await flush();
  return {
    order,
    afterReady,
    beforeProviderGrant,
    whilePresentedPending,
    whileGrantPending,
    value,
    destroyedBeforeClose,
    destroyedAfterClose: calls.filter(call => call[0] === "destroy").length,
    remainingListeners: [...listeners.values()].reduce((total, set) => total + set.size, 0),
  };
}

async function runTimeout() {
  let settled = false;
  let grantCallbackStartedAt = null;
  const pending = outcome(context.LectureSiftRewardedAds.show("/123/rewarded", {
    onGranted: () => {
      grantCallbackStartedAt = now;
      return new Promise(() => {});
    },
    timeoutMs: 15_000,
  })).then(result => {
    settled = true;
    return result;
  });

  await flush();
  await advance(5_000);
  pubads.emit("rewardedSlotReady", {makeRewardedVisible: () => true});
  pubads.emit("rewardedSlotGranted");
  await flush();
  await advance(9_999);
  const settledBeforeDeadline = settled;
  await advance(1);
  const result = await pending;
  return {
    result,
    grantCallbackStartedAt,
    settledBeforeDeadline,
    settledAfterDeadline: settled,
    destroyed: calls.filter(call => call[0] === "destroy").length,
    remainingListeners: [...listeners.values()].reduce((total, set) => total + set.size, 0),
  };
}

async function runProviderFailure() {
  let abandoned = 0;
  let releaseAbandon;
  let settled = false;
  const abandonResponse = new Promise(resolve => { releaseAbandon = resolve; });
  const pending = outcome(context.LectureSiftRewardedAds.show("/123/rewarded", {
    onAbandoned: async () => {
      abandoned += 1;
      await abandonResponse;
    },
    timeoutMs: 15_000,
  })).then(result => {
    settled = true;
    return result;
  });
  if (scenario === "provider_failure") {
    if (!providerScript?.onerror) throw new Error("provider script was not requested");
    providerScript.onerror();
  }
  await flush();
  const settledWhileAbandonPending = settled;
  releaseAbandon();
  const result = await pending;
  return {result, abandoned, settledWhileAbandonPending, pendingTimers: timers.size};
}

async function runCloseBeforeGrant() {
  const order = [];
  let releaseAbandon;
  let settled = false;
  const abandonResponse = new Promise(resolve => { releaseAbandon = resolve; });
  const pending = outcome(context.LectureSiftRewardedAds.show("/123/rewarded", {
    onPresented: () => { order.push("presented"); },
    onGranted: () => { order.push("granted"); },
    onAbandoned: async () => {
      order.push("abandoned:start");
      await abandonResponse;
      order.push("abandoned:end");
    },
    timeoutMs: 15_000,
  })).then(result => {
    settled = true;
    return result;
  });

  await flush();
  pubads.emit("rewardedSlotReady", {makeRewardedVisible: () => true});
  await flush();
  pubads.emit("rewardedSlotClosed");
  await flush();
  const settledImmediately = settled;
  await advance(4_999);
  const settledBeforeGrace = settled;
  const orderBeforeGrace = [...order];
  await advance(1);
  const settledWhileAbandonPending = settled;
  const orderWhileAbandonPending = [...order];
  releaseAbandon();
  const result = await pending;
  return {
    result,
    order,
    orderBeforeGrace,
    orderWhileAbandonPending,
    settledImmediately,
    settledBeforeGrace,
    settledWhileAbandonPending,
    destroyed: calls.filter(call => call[0] === "destroy").length,
    remainingListeners: [...listeners.values()].reduce((total, set) => total + set.size, 0),
  };
}

async function runCloseThenGrant() {
  const order = [];
  let settled = false;
  const pending = outcome(context.LectureSiftRewardedAds.show("/123/rewarded", {
    onPresented: () => { order.push("presented"); },
    onGranted: () => { order.push("granted"); },
    onAbandoned: () => { order.push("abandoned"); },
    timeoutMs: 15_000,
  })).then(result => {
    settled = true;
    return result;
  });

  await flush();
  pubads.emit("rewardedSlotReady", {makeRewardedVisible: () => true});
  await flush();
  pubads.emit("rewardedSlotClosed");
  await advance(4_999);
  const settledBeforeGrant = settled;
  pubads.emit("rewardedSlotGranted");
  const result = await pending;
  return {
    result,
    order,
    settledBeforeGrant,
    destroyed: calls.filter(call => call[0] === "destroy").length,
    remainingListeners: [...listeners.values()].reduce((total, set) => total + set.size, 0),
    pendingTimers: timers.size,
  };
}

async function runCloseAtDeadline() {
  let settled = false;
  const pending = outcome(context.LectureSiftRewardedAds.show("/123/rewarded", {
    timeoutMs: 15_000,
  })).then(result => {
    settled = true;
    return result;
  });

  await flush();
  pubads.emit("rewardedSlotReady", {makeRewardedVisible: () => true});
  await flush();
  await advance(10_000);
  pubads.emit("rewardedSlotClosed");
  await advance(4_999);
  const settledBeforeDeadline = settled;
  await advance(1);
  const result = await pending;
  return {
    result,
    settledBeforeDeadline,
    destroyed: calls.filter(call => call[0] === "destroy").length,
    remainingListeners: [...listeners.values()].reduce((total, set) => total + set.size, 0),
  };
}

async function runPostGrantCleanup() {
  const pending = context.LectureSiftRewardedAds.show("/123/rewarded", {timeoutMs: 15_000});
  await flush();
  pubads.emit("rewardedSlotReady", {makeRewardedVisible: () => true});
  pubads.emit("rewardedSlotGranted");
  const value = await pending;
  const destroyedAfterGrant = calls.filter(call => call[0] === "destroy").length;
  await advance(14_999);
  const destroyedBeforeDeadline = calls.filter(call => call[0] === "destroy").length;
  await advance(1);
  return {
    value,
    destroyedAfterGrant,
    destroyedBeforeDeadline,
    destroyedAtDeadline: calls.filter(call => call[0] === "destroy").length,
    remainingListeners: [...listeners.values()].reduce((total, set) => total + set.size, 0),
  };
}

const runners = {
  lifecycle: runLifecycle,
  timeout: runTimeout,
  provider_failure: runProviderFailure,
  provider_start_failure: runProviderFailure,
  close_before_grant: runCloseBeforeGrant,
  close_then_grant: runCloseThenGrant,
  close_at_deadline: runCloseAtDeadline,
  post_grant_cleanup: runPostGrantCleanup,
};
runners[scenario]().then(
  result => console.log(JSON.stringify(result)),
  error => { console.error(error); process.exitCode = 1; },
);
"""


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for rewarded-ad runtime tests")


def run_scenario(name: str) -> dict:
    completed = subprocess.run(
        [NODE, "-e", NODE_HARNESS, name, str(REWARDED_ADS)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_presented_event_precedes_grant_and_claim_waits_for_server_callbacks() -> None:
    result = run_scenario("lifecycle")

    assert result["afterReady"] == ["presented:start"]
    assert result["beforeProviderGrant"] == ["presented:start", "presented:pending"]
    assert result["whilePresentedPending"] == ["presented:start", "presented:pending"]
    assert result["whileGrantPending"] == [
        "presented:start",
        "presented:pending",
        "presented:end",
        "granted:start",
    ]
    assert result["order"] == [
        "presented:start",
        "presented:pending",
        "presented:end",
        "granted:start",
        "granted:end",
        "claim",
    ]
    assert result["value"] is True
    assert result["destroyedBeforeClose"] == 0
    assert result["destroyedAfterClose"] == 1
    assert result["remainingListeners"] == 0


def test_absolute_timeout_settles_when_a_server_callback_never_resolves() -> None:
    result = run_scenario("timeout")

    assert result["grantCallbackStartedAt"] == 5_000
    assert result["settledBeforeDeadline"] is False
    assert result["settledAfterDeadline"] is True
    assert result["result"] == {"status": "rejected", "error": "rewarded-ad-timeout"}
    assert result["destroyed"] == 1
    assert result["remainingListeners"] == 0


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ("provider_failure", "rewarded-ad-provider-unavailable"),
        ("provider_start_failure", "synthetic-provider-start-failure"),
    ],
)
def test_provider_failures_wait_for_abandonment_and_settle(scenario: str, message: str) -> None:
    result = run_scenario(scenario)

    assert result["result"] == {"status": "rejected", "error": message}
    assert result["abandoned"] == 1
    assert result["settledWhileAbandonPending"] is False
    assert result["pendingTimers"] == 0


def test_close_before_grant_settles_false_and_records_abandonment() -> None:
    result = run_scenario("close_before_grant")

    assert result["result"] == {"status": "fulfilled", "value": False}
    assert result["settledImmediately"] is False
    assert result["settledBeforeGrace"] is False
    assert result["orderBeforeGrace"] == ["presented"]
    assert result["settledWhileAbandonPending"] is False
    assert result["orderWhileAbandonPending"] == ["presented", "abandoned:start"]
    assert result["order"] == ["presented", "abandoned:start", "abandoned:end"]
    assert result["destroyed"] == 1
    assert result["remainingListeners"] == 0


def test_grant_arriving_during_close_grace_still_completes_without_abandonment() -> None:
    result = run_scenario("close_then_grant")

    assert result["settledBeforeGrant"] is False
    assert result["result"] == {"status": "fulfilled", "value": True}
    assert result["order"] == ["presented", "granted"]
    assert result["destroyed"] == 1
    assert result["remainingListeners"] == 0
    assert result["pendingTimers"] == 0


def test_close_grace_capped_by_absolute_deadline_settles_false_instead_of_timing_out() -> None:
    result = run_scenario("close_at_deadline")

    assert result["settledBeforeDeadline"] is False
    assert result["result"] == {"status": "fulfilled", "value": False}
    assert result["destroyed"] == 1
    assert result["remainingListeners"] == 0


def test_completed_grant_is_cleaned_up_at_the_absolute_deadline_without_close() -> None:
    result = run_scenario("post_grant_cleanup")

    assert result["value"] is True
    assert result["destroyedAfterGrant"] == 0
    assert result["destroyedBeforeDeadline"] == 0
    assert result["destroyedAtDeadline"] == 1
    assert result["remainingListeners"] == 0
