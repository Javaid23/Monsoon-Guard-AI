// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title ForecastRegistry
/// @notice Tamper-evident commit log for extreme-rainfall forecast records.
///
/// What this contract does and does not prove
/// ------------------------------------------
/// Records live off chain as canonical JSON in `records/`; only their SHA-256
/// hash is stored here. The chain therefore proves two things and nothing
/// more: that a record with exactly this content existed at the block
/// timestamp, and that it was committed *before the rain window it forecasts
/// began*. It does not prove the data was correct, the model was sound, or
/// that every forecast produced was submitted. (CLAUDE.md sections 10.1, 15.)
///
/// The timing rule is the point of the whole thing. A trust label is only
/// meaningful if it was fixed before the outcome could be known, so `commit`
/// refuses any record whose window has already started, and `recordOutcome`
/// refuses any outcome claimed before the window has closed. The caller never
/// supplies a time: both come from `block.timestamp`.
///
/// Because a forecast issued at 00 UTC for the window starting at 00 UTC
/// cannot physically be committed before that window opens (the forecast data
/// arrives hours later), the live ledger commits day-2 and day-3 windows only.
/// Day 1 is used in historical evaluation and must never be described as
/// pre-committed. (CLAUDE.md section 10.3.)
///
/// Append-only: a committed record is never altered. A correction is a new
/// record that references the old one, which keeps the mistake visible rather
/// than erasing it.
contract ForecastRegistry {
    /// @dev Owner is fixed at deployment. There is deliberately no transfer
    /// and no upgrade path: an owner who could be swapped, or logic that
    /// could be replaced, would weaken the only claim this contract makes.
    /// The trade-off is that a lost key ends the registry - which is the
    /// safer failure, since the existing history stays verifiable.
    address public immutable owner;

    struct Forecast {
        bytes32 forecastHash;      // SHA-256 of the canonical record JSON
        uint64 windowStart;        // unix seconds, inclusive
        uint64 windowEnd;          // unix seconds, exclusive
        uint64 committedAt;        // block.timestamp at commit
        bytes32 outcomeHash;       // SHA-256 of the outcome record, or zero
        uint64 outcomeRecordedAt;  // block.timestamp at outcome, or zero
    }

    /// @dev Keyed by keccak256 of the forecast id string. The readable id is
    /// kept in `ids` and in the events so the full history can be listed by
    /// anyone without prior knowledge of which ids exist.
    mapping(bytes32 => Forecast) private _forecasts;

    /// @notice Every committed forecast id, in commit order.
    string[] public ids;

    event ForecastCommitted(
        bytes32 indexed key,
        string forecastId,
        bytes32 forecastHash,
        uint64 windowStart,
        uint64 windowEnd,
        uint64 committedAt
    );

    event OutcomeRecorded(
        bytes32 indexed key,
        string forecastId,
        bytes32 outcomeHash,
        uint64 recordedAt
    );

    error NotOwner();
    error EmptyForecastId();
    error EmptyHash();
    error DuplicateForecastId(string forecastId);
    error WindowNotOrdered(uint64 windowStart, uint64 windowEnd);
    error WindowAlreadyStarted(uint64 now_, uint64 windowStart);
    error UnknownForecastId(string forecastId);
    error OutcomeAlreadyRecorded(string forecastId);
    error WindowNotClosed(uint64 now_, uint64 windowEnd);
    error IndexOutOfRange(uint256 index, uint256 length);

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    function key(string calldata forecastId) public pure returns (bytes32) {
        return keccak256(bytes(forecastId));
    }

    /// @notice Commit a forecast record hash before its rain window opens.
    /// @param forecastId   Human-readable id, e.g. "PK-NCEP-20260721-D2-REGION1".
    /// @param forecastHash SHA-256 of the canonical record bytes.
    /// @param windowStart  Start of the forecast window, unix seconds.
    /// @param windowEnd    End of the forecast window, unix seconds.
    function commit(
        string calldata forecastId,
        bytes32 forecastHash,
        uint64 windowStart,
        uint64 windowEnd
    ) external onlyOwner {
        if (bytes(forecastId).length == 0) revert EmptyForecastId();
        if (forecastHash == bytes32(0)) revert EmptyHash();

        bytes32 k = keccak256(bytes(forecastId));
        // A non-zero hash is the existence flag: committedAt alone would be
        // ambiguous, and overwriting a record would break append-only.
        if (_forecasts[k].forecastHash != bytes32(0)) {
            revert DuplicateForecastId(forecastId);
        }
        if (windowStart >= windowEnd) {
            revert WindowNotOrdered(windowStart, windowEnd);
        }

        uint64 nowTs = uint64(block.timestamp);
        // The commit must precede the window. Without this the timestamp
        // would prove nothing: anyone could "forecast" a window already under
        // way, or already finished.
        if (nowTs >= windowStart) {
            revert WindowAlreadyStarted(nowTs, windowStart);
        }

        _forecasts[k] = Forecast({
            forecastHash: forecastHash,
            windowStart: windowStart,
            windowEnd: windowEnd,
            committedAt: nowTs,
            outcomeHash: bytes32(0),
            outcomeRecordedAt: 0
        });
        ids.push(forecastId);

        emit ForecastCommitted(
            k, forecastId, forecastHash, windowStart, windowEnd, nowTs
        );
    }

    /// @notice Attach the observed outcome once the window has closed.
    function recordOutcome(string calldata forecastId, bytes32 outcomeHash)
        external
        onlyOwner
    {
        if (outcomeHash == bytes32(0)) revert EmptyHash();

        bytes32 k = keccak256(bytes(forecastId));
        Forecast storage f = _forecasts[k];
        if (f.forecastHash == bytes32(0)) revert UnknownForecastId(forecastId);
        if (f.outcomeHash != bytes32(0)) {
            revert OutcomeAlreadyRecorded(forecastId);
        }

        uint64 nowTs = uint64(block.timestamp);
        // An outcome claimed before the window closed cannot be an
        // observation of that window.
        if (nowTs < f.windowEnd) revert WindowNotClosed(nowTs, f.windowEnd);

        f.outcomeHash = outcomeHash;
        f.outcomeRecordedAt = nowTs;

        emit OutcomeRecorded(k, forecastId, outcomeHash, nowTs);
    }

    // --- reads ---------------------------------------------------------------

    function getForecast(string calldata forecastId)
        external
        view
        returns (Forecast memory)
    {
        return _forecasts[keccak256(bytes(forecastId))];
    }

    function getForecastByKey(bytes32 k)
        external
        view
        returns (Forecast memory)
    {
        return _forecasts[k];
    }

    function exists(string calldata forecastId) external view returns (bool) {
        return _forecasts[keccak256(bytes(forecastId))].forecastHash != bytes32(0);
    }

    function count() external view returns (uint256) {
        return ids.length;
    }

    /// @notice Page through the ids so the full history is readable without
    /// relying on event indexing.
    function idsSlice(uint256 start, uint256 limit)
        external
        view
        returns (string[] memory page)
    {
        uint256 len = ids.length;
        if (start > len) revert IndexOutOfRange(start, len);
        uint256 end = start + limit;
        if (end > len) end = len;
        page = new string[](end - start);
        for (uint256 i = start; i < end; i++) {
            page[i - start] = ids[i];
        }
    }
}
