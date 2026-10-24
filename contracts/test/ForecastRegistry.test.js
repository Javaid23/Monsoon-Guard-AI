// Tests for ForecastRegistry.
//
// CLAUDE.md section 10.4 names six cases that must be covered: duplicate id,
// empty hash, late commit, early outcome, double outcome, and a non-owner
// call. Each has a test below, plus the timing and listing behaviour the
// verification flow in section 10.5 depends on.
//
// Every timing test moves the chain clock rather than waiting, so the suite is
// deterministic and fast.

const { expect } = require("chai");
const { ethers } = require("hardhat");
const { time } = require("@nomicfoundation/hardhat-network-helpers");

const FORECAST_ID = "PK-NCEP-20260721-D2-REGION1";
const HOUR = 3600;
const DAY = 24 * HOUR;

// SHA-256 digests of canonical record bytes. Any non-zero bytes32 works here:
// the contract stores the hash, and recomputing it from the record is the
// verifier's job (section 10.5), not the chain's.
const FORECAST_HASH =
  "0x" + "11".repeat(32);
const OUTCOME_HASH =
  "0x" + "22".repeat(32);
const ZERO_HASH = "0x" + "00".repeat(32);

/** Deploy a fresh registry and return it with a window safely in the future. */
async function deployFixture() {
  const [owner, stranger] = await ethers.getSigners();
  const Registry = await ethers.getContractFactory("ForecastRegistry");
  const registry = await Registry.deploy();
  await registry.waitForDeployment();

  const now = await time.latest();
  // A day-2 window: opens tomorrow, closes the day after. This is the only
  // shape the live ledger commits, because a day-1 window has already opened
  // by the time the forecast data exists (section 10.3).
  const windowStart = now + DAY;
  const windowEnd = windowStart + DAY;

  return { registry, owner, stranger, now, windowStart, windowEnd };
}

describe("ForecastRegistry", function () {
  describe("deployment", function () {
    it("records the deployer as the immutable owner", async function () {
      const { registry, owner } = await deployFixture();
      expect(await registry.owner()).to.equal(owner.address);
    });

    it("starts empty", async function () {
      const { registry } = await deployFixture();
      expect(await registry.count()).to.equal(0n);
      expect(await registry.exists(FORECAST_ID)).to.equal(false);
    });
  });

  describe("commit", function () {
    it("stores the hash, the window and the chain's own timestamp", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();

      const tx = await registry.commit(
        FORECAST_ID, FORECAST_HASH, windowStart, windowEnd);
      const receipt = await tx.wait();
      const block = await ethers.provider.getBlock(receipt.blockNumber);

      const f = await registry.getForecast(FORECAST_ID);
      expect(f.forecastHash).to.equal(FORECAST_HASH);
      expect(f.windowStart).to.equal(BigInt(windowStart));
      expect(f.windowEnd).to.equal(BigInt(windowEnd));
      // The caller never supplies the commit time. That is the whole point of
      // the timestamp, so assert it came from the block.
      expect(f.committedAt).to.equal(BigInt(block.timestamp));
      expect(f.outcomeHash).to.equal(ZERO_HASH);
      expect(f.outcomeRecordedAt).to.equal(0n);
    });

    it("commits strictly before the window opens", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();
      await registry.commit(FORECAST_ID, FORECAST_HASH, windowStart, windowEnd);
      const f = await registry.getForecast(FORECAST_ID);
      expect(f.committedAt).to.be.lessThan(f.windowStart);
    });

    it("emits ForecastCommitted with the readable id", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();
      const k = await registry.key(FORECAST_ID);

      await expect(
        registry.commit(FORECAST_ID, FORECAST_HASH, windowStart, windowEnd)
      )
        .to.emit(registry, "ForecastCommitted")
        .withArgs(k, FORECAST_ID, FORECAST_HASH, windowStart, windowEnd,
                  anyUint64());
    });

    it("rejects a duplicate forecast id", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();
      await registry.commit(FORECAST_ID, FORECAST_HASH, windowStart, windowEnd);

      // Append-only: a correction must be a new record referencing the old
      // one, never an overwrite (section 10.1).
      await expect(
        registry.commit(FORECAST_ID, OUTCOME_HASH, windowStart, windowEnd)
      )
        .to.be.revertedWithCustomError(registry, "DuplicateForecastId")
        .withArgs(FORECAST_ID);

      expect(await registry.count()).to.equal(1n);
      const f = await registry.getForecast(FORECAST_ID);
      expect(f.forecastHash).to.equal(FORECAST_HASH); // unchanged
    });

    it("rejects an empty hash", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();
      await expect(
        registry.commit(FORECAST_ID, ZERO_HASH, windowStart, windowEnd)
      ).to.be.revertedWithCustomError(registry, "EmptyHash");
    });

    it("rejects an empty forecast id", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();
      await expect(
        registry.commit("", FORECAST_HASH, windowStart, windowEnd)
      ).to.be.revertedWithCustomError(registry, "EmptyForecastId");
    });

    it("rejects a late commit, after the window has opened", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();

      // Move past the window start. A record committed now could have been
      // written with knowledge of the weather, so the timestamp would prove
      // nothing.
      await time.increaseTo(windowStart + 1);

      await expect(
        registry.commit(FORECAST_ID, FORECAST_HASH, windowStart, windowEnd)
      ).to.be.revertedWithCustomError(registry, "WindowAlreadyStarted");
    });

    it("rejects a commit exactly at the window start", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();
      // The boundary must be strict: at windowStart the window has begun.
      // hardhat mines the tx one second after the forced timestamp, so aim
      // the clock to land the tx exactly on windowStart.
      await time.setNextBlockTimestamp(windowStart);
      await expect(
        registry.commit(FORECAST_ID, FORECAST_HASH, windowStart, windowEnd)
      ).to.be.revertedWithCustomError(registry, "WindowAlreadyStarted");
    });

    it("rejects a window that is not ordered", async function () {
      const { registry, windowStart } = await deployFixture();
      await expect(
        registry.commit(FORECAST_ID, FORECAST_HASH, windowStart, windowStart)
      ).to.be.revertedWithCustomError(registry, "WindowNotOrdered");

      await expect(
        registry.commit(
          FORECAST_ID, FORECAST_HASH, windowStart, windowStart - HOUR)
      ).to.be.revertedWithCustomError(registry, "WindowNotOrdered");
    });

    it("rejects a commit from anyone but the owner", async function () {
      const { registry, stranger, windowStart, windowEnd } =
        await deployFixture();
      await expect(
        registry
          .connect(stranger)
          .commit(FORECAST_ID, FORECAST_HASH, windowStart, windowEnd)
      ).to.be.revertedWithCustomError(registry, "NotOwner");
    });
  });

  describe("recordOutcome", function () {
    it("attaches the outcome once the window has closed", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();
      await registry.commit(FORECAST_ID, FORECAST_HASH, windowStart, windowEnd);

      await time.increaseTo(windowEnd + 1);
      const tx = await registry.recordOutcome(FORECAST_ID, OUTCOME_HASH);
      const receipt = await tx.wait();
      const block = await ethers.provider.getBlock(receipt.blockNumber);

      const f = await registry.getForecast(FORECAST_ID);
      expect(f.outcomeHash).to.equal(OUTCOME_HASH);
      expect(f.outcomeRecordedAt).to.equal(BigInt(block.timestamp));
      expect(f.outcomeRecordedAt).to.be.greaterThanOrEqual(f.windowEnd);
      // The forecast hash must survive untouched: the outcome is additional
      // evidence, not a revision.
      expect(f.forecastHash).to.equal(FORECAST_HASH);
    });

    it("allows an outcome exactly at the window end", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();
      await registry.commit(FORECAST_ID, FORECAST_HASH, windowStart, windowEnd);

      // windowEnd is exclusive, so the window is over at windowEnd itself.
      await time.setNextBlockTimestamp(windowEnd);
      await expect(registry.recordOutcome(FORECAST_ID, OUTCOME_HASH)).to.not.be
        .reverted;
    });

    it("emits OutcomeRecorded", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();
      await registry.commit(FORECAST_ID, FORECAST_HASH, windowStart, windowEnd);
      await time.increaseTo(windowEnd + 1);
      const k = await registry.key(FORECAST_ID);

      await expect(registry.recordOutcome(FORECAST_ID, OUTCOME_HASH))
        .to.emit(registry, "OutcomeRecorded")
        .withArgs(k, FORECAST_ID, OUTCOME_HASH, anyUint64());
    });

    it("rejects an early outcome, before the window closes", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();
      await registry.commit(FORECAST_ID, FORECAST_HASH, windowStart, windowEnd);

      // Mid-window: the rain has not finished, so this cannot be an
      // observation of the window.
      await time.increaseTo(windowStart + HOUR);
      await expect(
        registry.recordOutcome(FORECAST_ID, OUTCOME_HASH)
      ).to.be.revertedWithCustomError(registry, "WindowNotClosed");
    });

    it("rejects a second outcome for the same forecast", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();
      await registry.commit(FORECAST_ID, FORECAST_HASH, windowStart, windowEnd);
      await time.increaseTo(windowEnd + 1);
      await registry.recordOutcome(FORECAST_ID, OUTCOME_HASH);

      await expect(registry.recordOutcome(FORECAST_ID, FORECAST_HASH))
        .to.be.revertedWithCustomError(registry, "OutcomeAlreadyRecorded")
        .withArgs(FORECAST_ID);

      const f = await registry.getForecast(FORECAST_ID);
      expect(f.outcomeHash).to.equal(OUTCOME_HASH); // the first one stands
    });

    it("rejects an outcome for an unknown forecast", async function () {
      const { registry } = await deployFixture();
      await expect(registry.recordOutcome("PK-NCEP-NOPE-D2-REGION1", OUTCOME_HASH))
        .to.be.revertedWithCustomError(registry, "UnknownForecastId")
        .withArgs("PK-NCEP-NOPE-D2-REGION1");
    });

    it("rejects an empty outcome hash", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();
      await registry.commit(FORECAST_ID, FORECAST_HASH, windowStart, windowEnd);
      await time.increaseTo(windowEnd + 1);
      await expect(
        registry.recordOutcome(FORECAST_ID, ZERO_HASH)
      ).to.be.revertedWithCustomError(registry, "EmptyHash");
    });

    it("rejects an outcome from anyone but the owner", async function () {
      const { registry, stranger, windowStart, windowEnd } =
        await deployFixture();
      await registry.commit(FORECAST_ID, FORECAST_HASH, windowStart, windowEnd);
      await time.increaseTo(windowEnd + 1);

      await expect(
        registry.connect(stranger).recordOutcome(FORECAST_ID, OUTCOME_HASH)
      ).to.be.revertedWithCustomError(registry, "NotOwner");
    });
  });

  describe("history", function () {
    it("lists every committed id in commit order", async function () {
      const { registry, windowStart, windowEnd } = await deployFixture();
      const wanted = [
        "PK-NCEP-20260721-D2-REGION1",
        "PK-NCEP-20260721-D3-REGION1",
        "PK-NCEP-20260722-D2-REGION1",
      ];
      for (const id of wanted) {
        await registry.commit(id, FORECAST_HASH, windowStart, windowEnd);
      }

      expect(await registry.count()).to.equal(BigInt(wanted.length));
      expect(await registry.idsSlice(0, 10)).to.deep.equal(wanted);
      expect(await registry.idsSlice(1, 1)).to.deep.equal([wanted[1]]);
      // A visible, enumerable history is the only defence against silent
      // omission by the owner (section 10.6).
      expect(await registry.idsSlice(3, 5)).to.deep.equal([]);
    });

    it("rejects a slice starting past the end", async function () {
      const { registry } = await deployFixture();
      await expect(registry.idsSlice(1, 1)).to.be.revertedWithCustomError(
        registry,
        "IndexOutOfRange"
      );
    });

    it("returns an empty record for an unknown id rather than reverting", async function () {
      const { registry } = await deployFixture();
      // The verifier must be able to distinguish MISSED_COMMIT from a failure
      // to reach the chain (section 10.5), so a read of an absent id must
      // succeed and come back zeroed.
      const f = await registry.getForecast("PK-NCEP-NOPE-D2-REGION1");
      expect(f.forecastHash).to.equal(ZERO_HASH);
      expect(f.committedAt).to.equal(0n);
    });

    it("keys ids by keccak256 of the string", async function () {
      const { registry } = await deployFixture();
      expect(await registry.key(FORECAST_ID)).to.equal(
        ethers.keccak256(ethers.toUtf8Bytes(FORECAST_ID))
      );
    });
  });
});

/** Matcher for a block timestamp we do not want to pin exactly. */
function anyUint64() {
  return (value) => typeof value === "bigint" && value > 0n;
}

// --- archives ---------------------------------------------------------------
//
// Retrospective results cannot go through `commit`: their rain windows closed
// years ago, so the contract rightly refuses them, and a timestamp written now
// could not prove foreknowledge even if it accepted them. `publishArchive`
// makes the weaker, true claim instead - that this exact set of results
// existed unaltered at this block - and these tests pin both halves of that
// separation down.

/** Sorted-pair Merkle, matching the contract and OpenZeppelin's convention. */
function merkleRoot(leaves) {
  let level = [...leaves];
  if (level.length === 0) throw new Error("no leaves");
  while (level.length > 1) {
    const next = [];
    for (let i = 0; i < level.length; i += 2) {
      // An odd node is carried up unchanged rather than paired with itself,
      // which is what avoids the duplicate-leaf forgery in the naive scheme.
      if (i + 1 === level.length) next.push(level[i]);
      else next.push(hashPair(level[i], level[i + 1]));
    }
    level = next;
  }
  return level[0];
}

function hashPair(a, b) {
  const [lo, hi] = BigInt(a) <= BigInt(b) ? [a, b] : [b, a];
  return ethers.keccak256(ethers.concat([lo, hi]));
}

/** Proof for leaves[index], as sibling hashes from the bottom up. */
function merkleProof(leaves, index) {
  const proof = [];
  let level = [...leaves];
  let i = index;
  while (level.length > 1) {
    const next = [];
    for (let j = 0; j < level.length; j += 2) {
      if (j + 1 === level.length) {
        next.push(level[j]);
      } else {
        next.push(hashPair(level[j], level[j + 1]));
        if (j === i || j + 1 === i) proof.push(level[j === i ? j + 1 : j]);
      }
    }
    i = Math.floor(i / 2);
    level = next;
  }
  return proof;
}

// Thirteen leaves: the real shape of the 2022 replay, and deliberately odd so
// the carry-up branch is exercised.
const LEAVES = Array.from({ length: 13 }, (_, i) =>
  ethers.keccak256(ethers.toUtf8Bytes("PK-NCEP-2022080" + i)));

describe("publishArchive", function () {
  it("publishes a root and reports it", async function () {
    const { registry } = await deployFixture();
    const root = merkleRoot(LEAVES);

    await expect(registry.publishArchive(root, LEAVES.length, "jjas-2022-replay"))
      .to.emit(registry, "ArchivePublished");

    expect(await registry.archiveCount()).to.equal(1);
    const a = await registry.archives(0);
    expect(a.merkleRoot).to.equal(root);
    expect(a.leafCount).to.equal(LEAVES.length);
    expect(a.label).to.equal("jjas-2022-replay");
    expect(a.publishedAt).to.be.greaterThan(0);
  });

  it("verifies every record in the set against the root", async function () {
    const { registry } = await deployFixture();
    const root = merkleRoot(LEAVES);
    await registry.publishArchive(root, LEAVES.length, "jjas-2022-replay");

    for (let i = 0; i < LEAVES.length; i++) {
      expect(await registry.verifyArchiveProof(0, LEAVES[i], merkleProof(LEAVES, i)))
        .to.equal(true, "leaf " + i + " failed its own proof");
    }
  });

  it("rejects a record that is not in the set", async function () {
    const { registry } = await deployFixture();
    await registry.publishArchive(merkleRoot(LEAVES), LEAVES.length, "set");

    const forged = ethers.keccak256(ethers.toUtf8Bytes("PK-NCEP-NEVER-HAPPENED"));
    expect(await registry.verifyArchiveProof(0, forged, merkleProof(LEAVES, 0)))
      .to.equal(false);
  });

  it("rejects a tampered record under a valid proof", async function () {
    // The whole point: edit one number in a published record and the leaf
    // hash changes, so the proof that used to work no longer does.
    const { registry } = await deployFixture();
    await registry.publishArchive(merkleRoot(LEAVES), LEAVES.length, "set");

    const tampered = ethers.keccak256(ethers.toUtf8Bytes("PK-NCEP-20220800-edited"));
    expect(await registry.verifyArchiveProof(0, tampered, merkleProof(LEAVES, 0)))
      .to.equal(false);
  });

  it("refuses an empty root, an empty set and an empty label", async function () {
    const { registry } = await deployFixture();
    await expect(registry.publishArchive(ZERO_HASH, 1, "x"))
      .to.be.revertedWithCustomError(registry, "EmptyHash");
    await expect(registry.publishArchive(merkleRoot(LEAVES), 0, "x"))
      .to.be.revertedWithCustomError(registry, "EmptyArchive");
    await expect(registry.publishArchive(merkleRoot(LEAVES), 1, ""))
      .to.be.revertedWithCustomError(registry, "EmptyLabel");
  });

  it("refuses the same root twice", async function () {
    const { registry } = await deployFixture();
    const root = merkleRoot(LEAVES);
    await registry.publishArchive(root, LEAVES.length, "first");
    await expect(registry.publishArchive(root, LEAVES.length, "second"))
      .to.be.revertedWithCustomError(registry, "DuplicateArchiveRoot");
  });

  it("refuses a non-owner", async function () {
    const { registry, stranger } = await deployFixture();
    await expect(
      registry.connect(stranger)
        .publishArchive(merkleRoot(LEAVES), LEAVES.length, "x"))
      .to.be.revertedWithCustomError(registry, "NotOwner");
  });

  it("reverts on an out-of-range archive index", async function () {
    const { registry } = await deployFixture();
    await expect(registry.verifyArchiveProof(0, LEAVES[0], []))
      .to.be.revertedWithCustomError(registry, "IndexOutOfRange");
  });

  it("does not let an archive stand in for a commitment", async function () {
    // An archived record must still be absent from the forecast log, so the
    // verifier can never read a retrospective result as a pre-commitment.
    const { registry } = await deployFixture();
    await registry.publishArchive(merkleRoot(LEAVES), LEAVES.length, "replay");
    expect(await registry.exists("PK-NCEP-20220800")).to.equal(false);
    expect(await registry.count()).to.equal(0);
  });
});
