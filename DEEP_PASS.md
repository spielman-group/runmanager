# runmanager deep pass

The code-health campaign's pass over runmanager (`issues/code_health_campaign.md`
in the meta repo), on `CodeHealth`. Line references are to `57d3d21`, the merge of
Development's lab fixes into CodeHealth.

## Step 1: contract

What each existing test protects. Marks: **keep**, **rename** (the name does not
state the behaviour), **fix** (a real behaviour, but the test cannot fail when it
breaks, carries a stale or dead part, or pins a message shape), **cut** (protects
nothing worth keeping). Every test was written by the fork; upstream has none. A
"cannot fail" mark was shown by breaking the behaviour on a scratch copy and
running the test there.

### Totals

- Test files: 13, plus `fixtures.py` and `conftest.py`. Tests: 145, with 37 subtests.
- Marks: keep 111, rename 13, fix 17, cut 4. Approved by Ian and applied in
  9c36351, 19b8587 and 0b43a8c; the suite is now 141 tests, with 27 subtests. Each
  fix was shown to fail when its behaviour breaks. The entries below keep the old
  names, as the record of what was decided.

### For Ian: the non-keep marks

Cut:
- test_queueing `test_a_shot_written_earlier_keeps_the_extent_it_was_written_with`: its `n_runs` check repeats `test_an_added_shot_is_numbered_by_the_filename_it_is_given`; its earlier-file check cannot fail, since `name_run_files` opens no file.
- test_remote_api `test_pending_is_whether_the_shot_can_still_produce_a_result`, `test_the_queue_lists_each_row_as_its_status_gives_it` and `test_a_held_row_keeps_its_state_and_deleting_it_frees_the_rows_behind`: each repeats a stage of `test_every_record_is_one_the_table_allows_whatever_became_of_the_shot`, and the same mutations fail both.

Fix, because the test cannot fail when its behaviour breaks:
- test_queueing `test_no_default_shot_is_produced_while_the_queue_holds_work`: a running head is answered by the reclaim, never reaching the `n_items` guard; hold a rejected head instead.
- test_queueing `test_a_shot_that_compiles_is_offered_with_no_reason_on_it`: the "no reason" check cannot fail; assert the first answer is pending, and rename `test_a_lazy_shot_is_pending_until_compiled_then_offered`.
- test_queueing `test_the_anchor_survives_blacs_finding_the_queue_empty`: the default-labscript subtest sets no default file, so it leaves at the same early return; give it one.
- test_queueing `test_a_record_is_compiled_with_the_id_its_row_will_have`: assert on the records `compile_shots` returns, and rename `test_the_records_compile_shots_returns_carry_their_row_ids`.
- test_compiler_restart `test_a_restart_during_a_compile_does_not_wedge_later_compiles`: the old child is released at once, so removing the restart's fake reply still passes; keep the old child hung until the new one compiles (prototyped: fails without the reply).
- test_frozen_globals `test_a_scanned_value_compiles_back_to_exactly_what_was_frozen`: every check is inside a loop over `pending`; assert it holds two shots.
- test_remote_api `test_nothing_is_submitted_when_a_later_entry_would_expand`: `assertIn('3', ...)` is met by the entry's repr; check the refusal names the expanding global instead.
- test_remote_api `test_groups_are_listed_by_file_and_activated_by_file`: its unknown-group half is refused by the duplicate-name rule first; use a batch that is otherwise allowed.
- test_remote_api `test_a_command_waiting_on_a_preparse_is_answered_after_one_fails`: when broken it hangs in cleanup instead of failing; bound the wait.

Fix, because it pins a message or carries stale prose:
- test_analysis_submission `test_each_shot_file_is_reported_as_sent_rejected_or_not_sent`: pins 'Analysis is off.' and 'Cleared before it was sent to lyse.'; assert the kind and that a reason is given.
- test_remote_api `test_a_global_no_active_group_has_is_refused_before_anything_is_set`: pins the sentence and catches `Exception`; assert `ValueError` naming the global.
- test_queueing `test_a_failure_choosing_a_shot_is_reported_and_answered`: stale "no input makes choosing a shot raise"; drive it with a default labscript file that is gone, instead of overriding `offer_next`.
- test_remote_api `test_a_batch_is_distinct_shots_of_one_new_sequence_added_to_the_queue`, `test_a_later_submission_joins_the_sequence_it_names` and `test_adding_twice_before_the_first_compiles_does_not_reuse_its_numbers`: comments say a submitted shot has no queue row, which stopped being true.
- test_remote_api `test_a_scan_disabled_remotely_then_compiled_runs_the_default`: its comment narrates history.
- test_value_column `test_toggling_scan_commits_editor_and_swaps_active_expression`: an unused local `parent`.

Rename (proposed names in the entries below): test_queueing 8, test_remote_api 2, test_configuration 1, test_value_column 2.

Prose beside the tests, to correct with them: test_remote_api's file docstring (13 tests drive Engage on the app, not the client) and `ShotStatusTests`' docstring (`pending` is whether BLACS may still complete the shot, not "can still produce a result"); test_queueing's unused `ROW_BACKGROUNDS` import and the never-read `FakeRunManager.compiled`.

Gaps found, kept as notes (no test is added in this step): nothing in test_queueing sees a lyse outcome that arrives before its row retires; the remote tour never reaches a failed compile.

### tests/test_queueing.py

The runmanager-owned shot queue: `QueueController`, `QueueManager` and the queue widget in `runmanager/queueing.py`, and `RunManager`'s BLACS-exchange and batch-naming methods in `runmanager/__main__.py`, run on a `FakeRunManager` (RunManager's own methods over a real `QueueManager` and a real lyse; only the compile step and default-shot production are stood in). 59 tests.

#### QueueIdentityTests

Each queued shot has an id of its own, kept through save and restore, and a saved queue holds only work still to do.
- `test_queued_shots_have_distinct_ids_that_survive_save_and_restore`: every queued shot has its own id, and a restored queue has the same ids. **keep**.
- `test_running_and_failing_a_shot_does_not_change_the_saved_queue`: running and failing a row leaves the saved queue unchanged, so runmanager does not ask to save the configuration after each shot. **keep**. Broken on the copy by taking `state` out of `SESSION_ONLY_FIELDS`; it failed.
- `test_a_restored_record_without_an_id_is_given_one_and_keeps_the_rest`: a saved row with no id, or with a retired setting beside it, opens, is offerable with its other fields kept, and its new id is stable. **keep**.

#### QueuePauseTests

Pause is runmanager's policy on its own queue.
- `test_a_saved_queue_with_no_pause_state_loads_unpaused`: a configuration with no pause state opens with the queue running. **keep**. Broken on the copy by letting restore keep the old pause; it failed.
- `test_a_paused_queue_offers_no_shot_until_it_is_resumed`: a paused queue answers "paused" and leaves its row waiting; resuming offers the same row. **keep**. Broken on the copy by ignoring pause in `offer_shot`; it failed.

#### QueueOfferTests

What the queue offers BLACS, and when.
- `test_offered_shot_stays_at_the_head_of_the_queue`: the offered shot stays at the head of the queue, marked running. **keep**.
- `test_failed_row_is_offered_again_under_the_same_id`: a failed row is offered again under its id, runs like any shot, loses the old reason, and a second failure keeps its row. **keep**.
- `test_head_that_is_not_compiled_yet_is_not_offered`: a head not yet compiled is not offered, nor is a compiled row behind it. **keep**. Broken on the copy by dropping the `compiled` check in `offer_next`; it failed.

#### QueueOutcomeTests

What BLACS's outcome does to the row.
- `test_completed_shot_leaves_the_queue_and_the_next_one_is_offered`: a completed shot leaves the queue and the next one is offered. **keep**.
- `test_shot_that_did_not_complete_stays_queued_with_its_reason`: a failed, aborted or rejected shot stays at the head under its id with the reason; a rejected one shows as rejected. **keep**.

#### RejectedShotTests

A shot BLACS could not read is held at the head until an operator deletes it.
- `test_a_rejected_shot_is_not_offered_again`: a rejected shot is not offered again and holds its place against the rows behind it. **keep**. Broken on the copy by removing the rejected refusal from `REFUSED_STATES`; it failed.
- `test_deleting_it_lets_the_queue_go_on`: deleting the rejected row lets the next shot be offered. **rename** to `test_deleting_a_rejected_shot_lets_the_queue_go_on`: "it" is named only by the class. Broken on the copy by making `delete_rows` keep refused rows; it failed.

#### DefaultShotTests

A shot runmanager made for an empty queue is an ordinary row of the queue (take_default_shot is a stand-in that returns a prepared row).
- `test_a_default_shot_is_offered_as_a_row_of_the_queue`: a default shot is offered as a row of the queue, visible and running. **keep**.
- `test_a_completed_default_shot_leaves_the_queue_and_goes_for_analysis`: a completed default shot leaves the queue and is sent to lyse. **keep**.
- `test_a_default_shot_that_failed_stays_red_and_holds_back_the_next_one`: a failed default shot stays red, is not analysed, is offered again under its id, and no new default shot is made meanwhile. **keep**.
- `test_no_default_shot_is_produced_while_the_queue_holds_work`: no default shot is made while the queue holds work. **fix**: the scenario is a running head offered a second time, which `offer_next` answers by reclaiming the row, so `offer_shot` never reaches the `n_items` guard the test is named for. Broken on the copy by deleting that guard it still passed (exit 0); it fails only when reclaim breaks, which `test_a_lost_offer_reply_leaves_the_same_row_available` also covers. Hold a rejected head instead: a scratch variant of that test took 1 default shot instead of 0 with the guard removed, and passes with it present.
- `test_a_default_shot_is_not_saved_with_the_queue`: a default shot is left out of the saved queue and the real rows are kept. **keep**. Broken on the copy by saving default rows; it failed.

#### LazyCompileFailureTests

A queued shot that cannot be compiled leaves the queue with the reason, and a lazy one that compiles is offered (a real background compile with a stand-in compile step).
- `test_a_shot_that_cannot_be_compiled_leaves_with_its_reason`: a lazy shot whose compile fails or raises leaves the queue with the reason, answered "pending" and then the next shot. **keep**.
- `test_a_shot_that_compiles_is_offered_with_no_reason_on_it`: a lazy shot is compiled in the background and then offered as the running head. **fix**: the title and the `tooltip == path` assertion promise "no reason on it", which cannot fail, since a row not yet compiled never has a message. Broken on the copy by deleting `finish_compile`'s `state` and `message` reset, this and every other compile test in the file still passed (exit 0). Rename to `test_a_lazy_shot_is_pending_until_compiled_then_offered`, assert the first answer is "pending", drop the tooltip check.

#### CompiledFlagOwnershipTests

The controller alone records a compile, so a row cannot be offered half-compiled.
- `test_a_row_is_not_offerable_until_the_controller_records_the_compile`: a row whose compile has run but not been recorded is not offerable. **keep**. Broken on the copy by having `compile_shot` mark the row compiled; it failed.

#### SubmittedShotTests

A submitted batch has its rows at once, compiles in order, and a row that will not compile leaves (a real worker thread with a stand-in compile step the test can hold).
- `test_a_shot_just_submitted_has_its_row_at_once`: a submitted shot is in the queue, queued and pending, while its compile is still held. **keep**.
- `test_a_shot_that_completed_before_its_batch_did_is_finished_with`: a shot that completes while later shots of its batch are still compiling is reported left and completed, not pending. **keep**.
- `test_a_shot_that_will_not_compile_leaves_and_the_rest_still_run`: a shot that fails to compile leaves with the error and the shots around it still run. **keep**.
- `test_a_batch_that_is_not_going_to_the_queue_is_not_taken_on`: a batch not sent to BLACS queues nothing, so the queue knows no such shot. **rename** to `test_a_batch_not_sent_to_blacs_queues_nothing`: "is not taken on" says nothing. Broken on the copy by queueing every batch; it failed.

#### ContinuingSequenceAnchorTests

"Add shots to last sequence" carries on from the shot last sent, however the queue looks.
- `test_a_queue_with_work_in_it_is_what_is_continued`: with BLACS running the first shot, the sequence continues from the last waiting shot. **keep**. Broken on the copy by preferring the shot last sent; it failed.
- `test_a_default_shot_in_the_gap_does_not_become_what_is_continued`: a default shot filling the gap after a batch does not replace the submitted shot as the anchor. **keep**.
- `test_loading_a_configuration_mid_session_keeps_the_anchor`: loading a configuration without its rows keeps the shot last sent as the anchor. **keep**. Broken on the copy by having restore forget it; it failed.
- `test_the_anchor_survives_blacs_finding_the_queue_empty`: an empty queue does not let go of the shot last sent, under either empty-queue policy. **fix**: the default-labscript subtest sets no default labscript file, so `offer_shot` leaves at the same early return as under "nothing" and never reaches default-shot production. Made `take_default_shot` raise on the copy and both subtests still passed (exit 0). Set a default labscript file for that subtest, so a default shot is being prepared and none is offered.

#### ShotIdBeforeCompileTests

A shot has its id before anything writes its file.
- `test_a_record_is_compiled_with_the_id_its_row_will_have`: the records `compile_shots` takes carry the id their rows will have. **fix**: it reads the record the compile worker is given, which is the queue's own row, so it holds whatever `compile_shots` does; the caller's record is never looked at. Broken on the copy by deleting `record['shot_id'] = new_shot_id()` in `compile_shots` it still passed (exit 0), while the records returned to the caller lost their ids. Assert on the records `compile_shots` returns: a scratch variant failed with the line deleted and passes with it present. Rename to `test_the_records_compile_shots_returns_carry_their_row_ids`.
- `test_a_queued_shot_is_written_with_its_id`: a queued shot's file carries the id of its row. **keep**. Broken on the copy by not passing `shot_id` to `make_single_run_file`; it failed.

#### QueueEditingTests

Delete and Clear keep the shot BLACS has and take the rest.
- `test_delete_removes_the_rest_of_the_selection_around_it`: deleting a selection that includes the running row removes the waiting rows and their files and cancels, but keeps, the running one. **keep**. Broken on the copy by removing the running row on delete; it failed.
- `test_clear_keeps_the_shot_blacs_is_running_and_removes_the_rest`: Clear removes the waiting rows and their files and keeps the running one with its file. **keep**. Broken on the copy by clearing the sent row too, and by not deleting files; it failed each time.
- `test_clear_leaves_a_failed_row_alone_with_the_running_one`: Clear keeps a row that came back failed, red and with its file, and removes the waiting rows. **rename** to `test_clear_keeps_a_row_that_came_back_failed_and_removes_the_rest`: the test has no running row. Broken on the copy by clearing the sent row; it failed.
- `test_deleting_a_failed_row_uncovers_the_next_waiting_shot`: deleting a failed row removes it and its file, and the next request is offered the waiting shot. **keep**.
- `test_a_row_still_marked_running_can_be_deleted_after_a_restart`: a row stuck marked running comes back unmarked after a restart and can then be deleted. **keep**. Broken on the copy only when both the save and the restore kept `state` (either alone leaves the behaviour whole, and it passed); then it failed.

#### ReplayTests

A message BLACS sends again costs a poll, not a shot or the queue.
- `test_a_lost_offer_reply_leaves_the_same_row_available`: a request with no outcome after an offer is offered the same row under the same id. **keep**. Broken on the copy by not reclaiming a running head; it failed.
- `test_a_completed_outcome_that_arrives_twice_retires_the_row_once`: a completed outcome sent twice retires the row once and is passed to lyse both times. **keep**.
- `test_a_repeated_outcome_cannot_reach_a_later_shot_of_the_same_file`: a resent outcome cannot retire a later row queued for the same file, which has its own id. **keep**. Broken on the copy by matching rows by file name; it failed.
- `test_a_repeated_failed_outcome_keeps_one_red_row_and_one_reason`: a failed outcome sent twice leaves one red row, one reason, and one report to the operator. **keep**.
- `test_a_retry_that_fails_the_same_way_is_still_a_second_failure`: a retry that fails again for the same reason is reported again. **keep**.

#### OutcomeAppliedOnceTests

A failure while applying an outcome costs a re-run, not the data.
- `test_a_submission_that_falls_over_leaves_the_shot_to_be_run_again`: when the hand-off to lyse raises, BLACS is still answered and the row stays queued, to be offered again. **keep**. The raise is injected because the real hand-off only queues the path.
- `test_a_completion_that_named_no_file_is_reported_under_the_rows_own`: a completion naming no file goes to lyse under the row's own path, and the row is retired. **keep**. Broken on the copy by not reading the row's path; it failed.
- `test_a_completed_shot_lyse_took_is_recorded_as_sent`: once lyse takes a completed shot, its record says so after the row has left. **keep**. Broken on the copy by not updating the departed record; it failed.

#### CancelledShotTests

Deleting the shot BLACS was given cancels it until BLACS shows its file is free.
- `test_it_is_never_offered_again`: a cancelled shot is never offered, and at the next request the queue drops its row and offers the next shot. **rename** to `test_a_cancelled_shot_is_not_offered_and_goes_at_the_next_request`: "it" is named only by the class, and half the test is the row going. Broken on the copy by never dropping the cancelled head; it failed.
- `test_an_outcome_for_it_clears_it_and_only_a_completed_one_is_analysed`: any outcome for a cancelled shot clears its row, and only a completed one is sent for analysis. **rename** to `test_any_outcome_for_a_cancelled_shot_clears_it_and_only_a_completed_one_is_analysed`: "it" is named only by the class.

#### QueueBookkeepingUnderSubmissionTests

A default shot made because the queue looked empty does not outlive that.
- `test_a_default_shot_behind_real_work_is_discarded_with_its_file`: a default shot that real work has overtaken is dropped, with its file, when the next shot is offered. **keep**. Broken on the copy by not dropping overtaken default shots; it failed.

#### MalformedOutcomeTests

An outcome runmanager cannot read is refused and the exchange goes on.
- `test_an_outcome_runmanager_cannot_read_is_refused_and_answered`: a non-mapping, empty, shot-less, status-less or unknown-status outcome changes nothing and the reply still offers the shot. **keep**. Broken on the copy by raising on an unreadable outcome; every subtest failed.

#### SentToBlacsRowTests

The shot BLACS was given is the widget's reserved first row.
- `test_the_shot_that_was_sent_moves_into_the_reserved_row`: the shot sent to BLACS is drawn in the reserved first row and not listed twice. **keep**. Broken on the copy by never reserving the row; it failed.

#### QueueDisplayTests

A failed row is tinted, and its text stands off the tint.
- `test_a_tinted_row_names_its_text_colour_too`: a failed row is tinted with a text colour that stands off the fill, and an untinted row is left to the theme. **keep**. Broken on the copy by not naming the text colour; it failed.

#### ExchangeFailureTests

A fault on runmanager's side must not read to BLACS as an outage.
- `test_a_failure_choosing_a_shot_is_reported_and_answered`: a failure while choosing a shot is reported and answered normally, and the outcome that came with the request is still applied. **fix**: the comment says "no input makes choosing a shot raise", which is not true: with the default-labscript policy and a default labscript file that no longer exists, `offer_shot` raises `RuntimeError` on real input. The test replaces `queue_manager.offer_next` with a raiser instead. A scratch variant using the missing file passes, and fails (exit 1) with `queue_exchange` no longer catching, as the present test also does. Drive it with the missing file.

#### LostRowTests

A completed shot that matches no row must not vanish quietly.
- `test_a_completed_shot_with_no_row_is_still_analysed`: a completed shot whose row has gone is still sent to lyse. **keep**.

#### SequenceContinuityTests

Shots added to the last sequence keep its attributes and continue its run numbers, and claim no new sequence index.
- `test_an_added_shot_is_numbered_by_the_filename_it_is_given`: shots added after `experiment_03` get `_04` and `_05` with the same run numbers, `n_runs` 6, and the sequence's attributes. **rename** to `test_shots_added_to_a_sequence_continue_its_run_numbers_and_keep_its_attributes`: the name covers one of its three assertions. Broken on the copy by giving added shots run number 0; it failed.
- `test_a_shot_written_earlier_keeps_the_extent_it_was_written_with`: a batch added after an existing shot gets `n_runs` 3 and the earlier file is left as written. **cut**: its `n_runs` assertion repeats the previous test's, and the two failed together when `n_runs` was made one too many on the copy. The earlier file cannot change in this call: `name_run_files` names files and opens none (a scratch variant with `h5py.File` made to raise during the add still passed), so the assertion on it can only fail if rewriting is added.
- `test_adding_shots_to_a_sequence_claims_no_new_sequence_index`: adding shots to a sequence does not use up a new sequence index. **keep**. Broken on the copy by claiming an index when adding; it failed.
- `test_a_batch_of_its_own_does_claim_a_sequence_index`: a batch that joins nothing claims the next sequence index, which also shows the test above can see a claim. **keep**.
- `test_a_replacement_batch_resumes_after_the_shots_blacs_has`: after Clear, a replacement batch is numbered after the shot BLACS has and takes the waiting shot's number back. **keep**. Broken on the copy by not deleting cleared files; it failed.

#### DeletedAnchorTests

The shot last sent stays the end of its sequence whatever becomes of its row and file.
- `test_deleting_the_failed_shot_it_named_keeps_the_anchor`: deleting the failed shot last sent removes its file but leaves it the anchor, with its sequence attributes. **rename** to `test_deleting_the_failed_shot_last_sent_keeps_it_as_the_anchor`: "it named" is unclear. Broken on the copy by having delete forget the shot last sent; it failed.
- `test_deleting_another_shot_leaves_the_anchor_alone`: deleting a waiting shot does not change the shot last sent. **keep**. Failed under the same break.
- `test_a_cancelled_shots_file_going_leaves_the_anchor`: a cancelled shot released at the next request loses its file and is still the anchor. **rename** to `test_a_cancelled_shot_whose_file_goes_is_still_the_anchor`: "leaves" reads as departing. Failed under the same break.

#### Not tests

- `FakeRunManager.compiled` is never read, and the comment saying it is "recorded so a test can say how many times it was asked" is stale; `SubmittedShotTests` keeps its own list. The import `ROW_BACKGROUNDS` is unused.
- Not covered: a lyse outcome arriving before its row retires (the `lyse=item['lyse']` carried into the departed record in `shot_finished`); breaking it passes, since the test sees the other ordering.

#### Totals

- Tests: 59 (14 subtests). Marks: keep 45, rename 8, fix 5, cut 1.

### tests/test_remote_api.py

Runmanager's remote commands, sent by the real `RunmanagerClient` to a real `RunmanagerServer` over a free loopback port; the application behind it is `FakeApp` (a real `QueueManager`) or `SubmittingApp` (real `RunManager` methods over a stand-in window). 48 tests, 17 subtests. The file docstring says each test is sent by the client, but 13 are not: all of `SubmissionAnchorTests` except its last, both `ShuffledEngageTests` and `CompileOnlyEngageTests` drive `RunManager.engage` or `on_engage_clicked` on the app directly, as the operator's Engage button does. They protect real behaviour and are marked below on that; the docstring needs correcting, or they belong in an engage test file.

#### SubmitShotsTests: `submit_shots` by globals that differ per entry; a refused call leaves the queue and the window as it found them (two tests, the raw comment and `skip_missing`, are `set_values` behaviour)

- `test_a_batch_is_distinct_shots_of_one_new_sequence_added_to_the_queue`: a batch's shots get distinct paths, shot ids and run numbers in one new sequence of their own, and join the queue without replacing it. **fix**. Its comment says a submitted shot "has no file and no queue row until the worker reaches it"; `QueueManager.compile_shots` queues the row at submission (a probe in the copy: both paths in `get_queue_paths()` straight after `submit_shots` with the compile blocked, files not yet written). The assertions are sound: making a first submission join the queue's sequence fails this test and three more. Reword the comment.
- `test_a_batch_of_no_entries_submits_nothing_and_says_so`: an empty entry list returns `[]` and makes no batch. **keep**.
- `test_a_later_submission_joins_the_sequence_it_names`: a submission naming an earlier one's `sequence_id` is numbered after it, in its folder, though the first is still compiling. **fix**. Its comment says the first batch's shots "have neither rows nor files"; the rows exist from submission (see the first entry). Reword the comment; the assertions stay.
- `test_a_session_joins_its_own_sequence_when_another_shares_its_id`: with the sequence index, a join finds the session's sequence even when an operator's Engage in the same second has the same `sequence_id`. **keep**. Breaking the index match (join by id alone) makes it fail.
- `test_a_joined_shot_is_named_from_its_own_globals`: a joined shot's folder and filename are made from the sequence's formats and its own globals. **keep**.
- `test_a_join_is_refused_once_the_labscript_file_has_changed`: a join onto a sequence of another labscript file raises `SequenceRefused` and queues nothing. **keep**. Removing the check makes it fail.
- `test_a_sequence_runmanager_has_no_record_of_is_refused`: naming an unknown sequence raises `SequenceRefused` instead of starting afresh. **keep**.
- `test_a_submission_that_raises_has_queued_nothing_at_all`: a submission that fails (labscript file cleared) leaves the queue as it was. **keep**. Moving the raise to after the batch is queued makes it fail.
- `test_the_window_is_left_holding_the_last_shot_submitted`: after a batch the globals hold the last entry's values, comments kept. **keep**. Dropping the `set_values` of the last entry makes it fail.
- `test_nothing_is_submitted_when_a_later_entry_would_expand`: an entry that would be more than one shot refuses the whole batch, naming the global whose scan caused it and not an innocent one. **fix**. `assertIn('3', ...)` is met by the entry's repr `{'x': 3}`, so it never checks the count (with the count in the message replaced by 0 the test still passes). Drop it, or assert the count separately; the `y` named and `depth` not named assertions do the real work.
- `test_an_error_in_the_globals_is_refused_before_anything_is_submitted`: a global that does not evaluate refuses the batch, naming it. **keep**.
- `test_a_global_no_active_group_has_is_refused_before_anything_is_set`: an entry naming a global in no active group is refused before any set. **fix**. It pins the exact sentence 'Global not_a_global not found in any active group'; rewording the message to 'No global named not_a_global is active' (same `ValueError`, same global named) fails it. Assert `ValueError` (it takes `Exception` now) and that the name appears. If wording parity with `set_values` is wanted, compare against `set_values`'s own refusal text, not a literal.
- `test_a_global_with_scan_or_jit_ticked_is_refused_before_anything_is_set`: a global with Scan? or JIT? ticked refuses the batch, naming both, and sets nothing. **keep**.
- `test_a_raw_expression_with_its_own_comment_is_written_as_given`: `set_values(raw=True)` of an expression with its own comment does not stack the old comment. **keep**. Filed under `submit_shots` but a `set_values` behaviour.
- `test_skip_missing_writes_the_globals_that_exist_and_returns_the_rest`: `set_values` refuses a batch with an unknown global and writes nothing; with `skip_missing` it writes the rest and returns the names skipped. **keep**. Filed under `submit_shots` but a `set_values` behaviour.

#### ShotStatusTests: `shot_status` and `get_queue`: how far each shot has got, whether it is blocked, and what became of it when it left

Class docstring and the `EXPECTED` comment: **fix**. Both speak of "whether a shot ... can still produce a result" and "whether a caller should go on waiting for a shot". The client's table says `pending` is "whether the shot may still reach blacs 'completed' ... and nothing about lyse": a shot BLACS completed has left the queue, so `pending` is False while its lyse result is still to come. Say "BLACS can still complete it".

Helpers `steps`, `is_legal`, `may_still_complete`: checked line by line against the table and `pending` text in `RunmanagerClient.shot_status`. Not stale. In queue: lyse 'waiting', blacs not 'waiting' means compile 'compiled', rejected means not pending, cancelled means pending, blocked means not pending. Left: never pending; blacs 'completed' means compile 'compiled' and lyse one of the four values; any other blacs means lyse None; compile 'failed' means blacs None. `is_legal` also refuses compile 'failed' for a row still in the queue, which the client's prose gives ("leaves the queue at once") though its table does not. `may_still_complete` is queued and not rejected, which matches. Below, the tour is `test_every_record_is_one_the_table_allows_whatever_became_of_the_shot`. Rows reach their states in the other tests by writing the state into the controller's `_items`; only the tour goes through `offer_next`, `shot_finished` and `delete_rows`.

- `test_pending_is_whether_the_shot_can_still_produce_a_result`: `pending` for one row in each state: true for waiting, running, failed and cancelled, false for rejected. **cut**. Duplicates `test_every_record_is_one_the_table_allows_whatever_became_of_the_shot`, which reaches each of those states through the real transitions and asserts `pending` at each. Evidence: setting `pending` True for every refused-state row in `QueueController._row_statuses` fails this test and fails that one. If it is kept instead, it needs a fix to its name (`pending` is not about a result; see the class note) and stop writing `state` into `_items`.
- `test_a_row_behind_a_held_one_says_it_is_blocked`: a row behind a rejected one is blocked and not pending; behind a waiting, running, failed or cancelled one it is queued and pending. **keep**. Evidence: making a cancelled head hold up the rows behind (`REFUSED_STATES[BLACS_CANCELLED] = True`) fails this test and no other, the tour included.
- `test_the_queue_lists_each_row_as_its_status_gives_it`: `get_queue` and `shot_status` give the same record for a row, blocked rows included. **cut**. Duplicates `test_every_record_is_one_the_table_allows_whatever_became_of_the_shot`, whose `ask()` asserts `listed == [answer[...]]` at every stage; both calls are one function (`_row_statuses`). Evidence: making `get_queue` say 'queued' for every row fails both.
- `test_a_held_row_keeps_its_state_and_deleting_it_frees_the_rows_behind`: a rejected head keeps 'rejected' while the row behind is blocked; deleting it frees that row. **cut**. Duplicates the tour, which does exactly this sequence (retried rejected, behind blocked, delete, behind queued and pending, retried left with blacs 'rejected'). Evidence: making `delete_rows` keep a rejected row fails both.
- `test_a_completed_shots_lyse_outcome_is_recorded_whenever_it_arrives`: lyse's outcome for a completed shot is recorded whether it arrives before or after the shot leaves the queue. **keep**. Evidence: ignoring the late outcome fails the 'after' subtest; losing the early one fails the 'before' subtest.
- `test_a_shot_the_queue_no_longer_has_is_not_pending`: a shot that left is recorded by how it left (completed with lyse waiting; deleted or emptied with no blacs and no lyse), none pending; an id never heard of has no record. **rename**. Proposed name `test_a_shot_that_left_is_told_apart_by_how_it_left_and_an_unknown_id_has_no_record`; the name states only the `pending` half.
- `test_every_record_is_one_the_table_allows_whatever_became_of_the_shot`: four shots taken through rejection and deletion, completion and lyse, cancellation, and emptying while compiling: each stage's record is the one expected, a legal combination, `pending` agrees with BLACS being able to complete, and `since` advances at every change. **rename**. Proposed name `test_a_shots_record_advances_step_by_step_and_is_always_a_legal_one`; the name covers the legality check alone. Gap, not a mark: no stage reaches a failed compile, so the table's "left, compile 'failed', blacs None" row is never run through `is_legal` (`CompileErrorTests` reaches it without the check).

#### EmptyQueueTests: the remote abort ("Empty queue") backs out of waiting work

- `test_a_batch_still_compiling_goes_with_its_files`: abort empties the queue, the batch's second shot never compiles, and the first's file is removed once its compile ends. **keep**. Evidence: not deleting the file of a compile that finishes after its row went makes it fail.

#### SubmissionAnchorTests: the shot each Engage submission mode numbers its batch after (all but the last drive `engage` on the app, not the client)

- `test_a_replacement_takes_no_name_of_a_shot_still_compiling`: a replacement batch skips the filename of a shot still compiling, numbering from 0 again. **keep**. Evidence: not skipping compiling names makes it fail and no other test.
- `test_a_join_after_a_replacement_numbers_after_both_batches`: a join after a replacement is numbered after both batches. **keep**. Evidence: the next run number forgetting the earlier batch, or a join not using the recorded sequence, fails it.
- `test_adding_twice_before_the_first_compiles_does_not_reuse_its_numbers`: two "add to last sequence" Engages in a row give distinct, consecutive run numbers and paths. **fix**. Its comment says the first batch's shot has "no row and no file, so the queue's last row is still the shot it was added to"; the row is queued at submission, so the premise is stale. Either numbering mechanism on its own now suffices (the queue row as anchor, or the recorded sequence): with the recorded-sequence join disabled the test still passes, three runs. Restate the behaviour without the stale cause.
- `test_adding_to_the_last_sequence_carries_on_from_the_shot_blacs_has`: with the queue empty, "last sequence" joins the sequence of the shot last sent to BLACS. **keep**.
- `test_adding_to_the_last_sequence_joins_one_still_compiling`: "last sequence" joins the sequence just submitted, compiled or not. **keep**.
- `test_adding_to_nothing_at_all_starts_a_sequence`: with nothing queued or sent, "last sequence" starts sequence 0. **keep**.
- `test_replacing_nothing_at_all_starts_a_sequence`: the same for the replace mode. **keep**.
- `test_a_replacement_onto_a_row_with_no_sequence_is_refused_before_the_clear`: a replacement anchored on a restored row with no sequence on record raises `SequenceRefused`, and the queue and the file are untouched. **keep**.
- `test_the_queue_is_read_once_for_the_sequence_being_added_to`: a batch acts on one read of the queue, so a queue BLACS empties mid-batch does not turn "add to last sequence" into a new sequence. **keep**. Evidence: reading the queue twice in `get_submission_anchor` makes it fail. It simulates the race by replacing `app.get_queue_append_filepath`, so it is tied to that method name.
- `test_a_replacement_batch_joins_the_sequence_blacs_is_running`: a replacement joins the sequence BLACS is running, throws away the waiting work, and takes back its run numbers. **keep**.
- `test_a_remote_engage_carries_out_the_mode_and_receipts_each_shot`: a remote `engage(mode)` carries out the mode and returns a receipt per shot, and the queue lists those shots. **keep**. Evidence: the server dropping the mode fails it.

#### ShuffledEngageTests: Engage with the shuffle button down or an axis's box checked (drives Engage on the app)

- `test_a_shuffled_batch_keeps_each_shot_named_for_the_globals_it_carries`: with the shuffle button down, the batch is queued in shuffled order and each file is named for the globals compiled into it. **keep**. Evidence: ignoring the button fails it.
- `test_an_axis_is_shuffled_only_when_its_shuffle_box_is_checked`: an unchecked axis keeps its order; a checked one is shuffled. **keep**. Evidence: ignoring the box fails it.

#### CompileOnlyEngageTests: Engage with neither destination ticked (drives Engage on the app)

- `test_the_shots_are_compiled_and_go_nowhere`: with Run shots and View shots off, the batch is compiled, its files kept, and nothing is queued or sent to runviewer. **keep**. Evidence: queueing the batch anyway makes it fail.

#### CompileErrorTests: what a remote caller is told when a compile fails

- `test_a_shot_that_fails_to_compile_leaves_the_queue_with_its_error`: a queued shot that fails to compile leaves the queue at once, the error in its message. **keep**.
- `test_a_test_compile_answers_with_the_error_and_queues_nothing`: `test_compile` compiles the window's first shot into a scratch file, answers with the failure, and queues nothing, claims no sequence, touches no folder. **keep**. Evidence: claiming a sequence index makes it fail.

#### DisabledScanTests: a scan turned off remotely stops expanding the shots compiled

- `test_a_scan_disabled_remotely_then_compiled_runs_the_default`: after `set_scan_enabled` off, `test_compile` runs the default even when the axes list still holds the scan's axis. **fix**. The comment narrates history ("as a write collision once made it"); say what is true now. The test is the only guard on a stale axis: dropping the filter in `expand_globals` that skips an ordered axis the globals no longer produce fails this test and no other.

#### GlobalEditTests: expansions, units, and creating and deleting globals, in the file and in an open tab

- `test_an_expansion_set_remotely_is_read_back`: `set_expansions` is read back by `get_expansions`, and a non-string expansion raises `TypeError`. **keep**.
- `test_units_are_read`: `get_units` returns the units of the active globals. **keep**.
- `test_a_global_created_remotely_has_its_value_and_units_and_is_not_made_twice`: `create_global` stores the value and units, and creating it again raises. **keep**.
- `test_a_global_deleted_remotely_is_gone_and_an_unknown_one_is_refused`: `delete_global` removes a global; deleting an unknown one raises `ValueError`. **keep**.
- `test_edits_to_a_group_open_in_a_tab_show_in_the_tab`: remote creation, expansion and deletion show in a group tab that is open. **keep**. Evidence: skipping the tab's units update fails it.

#### GroupActivationTests: groups of one name in two open files are told apart by file

- `test_groups_are_listed_by_file_and_activated_by_file`: `get_groups` lists by file and `set_groups_active` activates by file; a batch naming an unknown group changes none. **fix**. The last assertion cannot fail for the reason it states. File A's `g` is already active, so the batch's `{file_b: {'g': True}}` half is refused by the two-active-groups rule whatever happens to the unknown `h`. Evidence: replacing the unknown-group refusal by skipping it leaves the test passing. Use a batch whose valid half would succeed alone, such as `{file_a: {'g': False, 'h': True}}`, and assert `g` is still active.
- `test_two_active_groups_of_one_name_are_refused`: activating a second group of the same name raises `ValueError` and changes nothing. **keep**. Evidence: removing the check fails it.

#### PreparseFailureTests: a command waiting on the preparse is answered even if the preparse fails

- `test_a_command_waiting_on_a_preparse_is_answered_after_one_fails`: after a failed preparse, `n_shots` is still answered and the failure still reported. **fix**. It fails as a hang. With the `task_done` in `preparse_globals_loop`'s `finally` removed, the assertion fails, then the cleanup `server.shutdown` joins the handler stuck in `wait_until_preparse_complete` and never returns (a faulthandler dump shows it; the run had to be killed, exit 143). Release the waiter before the server shutdown in the test's cleanups (cleanups run in reverse), so a regression reports a failure.

#### Mutations run in a scratch copy of CodeHealth (57d3d21), each restored afterwards; pytest's exit status read unpiped

Positive control: a mutation in the copy's `queueing.py` failed the expected tests, so the copy's runmanager is the one imported. Broken, one at a time: a rejected row's `pending` set True; delete of a rejected row refused; `get_queue` rows all 'queued'; `REFUSED_STATES[BLACS_CANCELLED]` True; the departed lyse outcome ignored; the carried lyse outcome dropped; late-compile file not deleted; Run shots off still queues; stale-axis filter in `expand_globals` removed; compiling names not skipped; next run number forgetting earlier batches; recorded-sequence join disabled (three runs); Shuffle button ignored; axis shuffle box ignored; `get_submission_anchor` reading twice; join ignoring the sequence index; labscript-file check removed; first submission joining the queue's sequence; last entry not set in the window; unknown-global message reworded; shot count in the expand message replaced by 0; raise moved after the queue; test-compile claiming a sequence index; remote engage dropping its mode; tab units update skipped; duplicate-group check removed; unknown-group refusal turned into a skip; `task_done` removed from the preparse loop. A probe subclass of `SubmitShotsTests` confirmed rows are queued at submission.

- Test files: 1. Tests: 48, with 17 subtests.
- Marks: keep 35, rename 2, fix 8, cut 3.

### tests/test_analysis_submission.py

Shots runmanager's `AnalysisSubmission` sends to lyse, through a real `LyseClient` to a
real `LyseServer`; lyse's window is a stand-in holding the incoming queue.
- `test_a_submitted_shot_reaches_lyse_as_its_local_path`: a completed shot handed to the submission arrives in lyse's incoming queue as this machine's path. **keep**.
- `test_a_shot_lyse_refuses_is_dropped_not_retried`: a shot lyse answers with a refusal is offered once and not again, so it cannot go ahead of the next shot. **keep**.
- `test_each_shot_file_is_reported_as_sent_rejected_or_not_sent`: each shot file is settled with one outcome (sent; rejected with lyse's reason; not sent because Analyse is off or because it was cleared while lyse was away), and a connection failure is retried, not reported. **fix**. It pins message text at two places: `'Analysis is off.'` and `'Cleared before it was sent to lyse.'`. Rewording either string in `analysis_submission.py` fails the test with the behaviour unchanged (both mutated, see Evidence). Assert the path, the kind `'not sent'` and that a reason is given, not its words. The `assertIn('lyse cannot take shots', ...)` stays: that is lyse's own reason relayed, and dropping the relay fails it.
- `test_a_shot_waits_while_lyse_is_away_and_goes_once_it_is_back`: a shot sent while lyse is down is kept, and goes to lyse once it is back and a retry is made. **keep**.

### tests/test_blacs_status.py

`blacs_state`: what runmanager says BLACS is doing, worked out from BLACS's `get_status` answer. The output is words shown beside the BLACS light, so the words are what is asserted (the tooltip and stop-reason checks only loosely).
- `test_the_state_says_whether_blacs_is_asking_for_work`: with no shot running, the state says whether BLACS is requesting shots. **keep**.
- `test_a_blacs_running_a_shot_names_the_shot`: a BLACS running a queued shot says `running <file name>`, even while it still requests more. **keep**. The base case: the next two repeat its state assertion for their variants.
- `test_a_shot_blacs_ran_on_its_own_is_told_apart_from_queue_work`: a shot with no id is the BLACS local override, and the tooltip says it is not a queued shot. **keep**.
- `test_a_shot_path_from_another_machine_still_names_the_shot`: a shared-drive-agnostic Windows path still names its file and is shown in this machine's form. **keep**.
- `test_the_reason_blacs_stopped_is_in_the_state`: when BLACS has stopped with an error, the state says it stopped and gives the reason. **keep**.

### tests/test_compiler_restart.py

Restarting the compiler subprocess while a shot is compiling; the child is a thread at the far end of real zprocess queues, the rest is runmanager's own.
- `test_a_restart_during_a_compile_does_not_wedge_later_compiles`: pressing Restart subprocess mid-compile frees that compile and sends the next shot to the new child, not the old one. **fix**. The compile in flight is freed only by the fake `['done', False, ...]` reply that `on_restart_subprocess_clicked` puts on the queue, and that reply can be deleted with the test still passing: the test calls `release.set()` straight after the restart, so the old child finishes the compile itself and replies (mutated, exit 0). The case Restart exists for, a child that is hung, is not tested. Fix: keep the old child hung until the new one has compiled the later shot (`release.set()` as a cleanup, not before), give `FakeChild` a `terminate()` that ends it, because runmanager terminates a child that does not quit within 2 s, and optionally assert the first compile came back failed. I tried this in the scratch copy: it passes on the real code and fails with the fake reply removed. The other three parts of the restart (holding `child_ready`, telling the child to quit, marking the new child ready) are enforced already.

### tests/test_configuration.py

The shots waiting in the queue, in a saved configuration.
- `test_queued_shots_come_back_at_startup_and_a_later_load_leaves_them`: a saved configuration brings its queued shots back when runmanager starts, a load during a session leaves the session's own queue, and the saved queue settings (paused) apply either way. **rename**. The name omits the third behaviour, the settings applied on every load. Proposed: `test_queued_shots_return_only_at_startup_but_queue_settings_load_every_time`.

### tests/test_frozen_globals.py

Shots frozen from scanned globals compile back to what was scanned.
- `test_a_scanned_value_compiles_back_to_exactly_what_was_frozen`: the text a scanned value is frozen as, when compiled, gives back every digit and every element of the value, including a 2000-element array. **fix**. The assertions sit in `for shot, frozen in pending:` and nothing asserts `pending` has any shots, so the test passes when `get_shots_with_frozen_globals` returns no shots at all, or only the first of the two (both mutated, exit 0). Add `self.assertEqual(len(pending), 2)` before the loop. As written it does fail on the real fault: plain `repr` instead of `np.printoptions(..., floatmode='unique')`, or without `threshold=sys.maxsize`, both exit 1.

### tests/test_globals_file.py

Edits to a TOML globals file.
- `test_edits_from_two_threads_all_persist`: two threads editing different globals of one file at once, as a remote call and the preparse do, lose none of the edits. **keep**. Removing the lock fails it every time (3 of 3 runs).

### tests/test_legacy_globals_file.py

Legacy HDF5 globals files, which are read-only until converted to TOML.
- `test_opening_a_legacy_file_with_a_list_global_stores_nothing`: opening a legacy file whose global is a list does not try to store an expansion guess in it. **keep**.
- `test_a_global_the_file_expands_arrives_as_a_scan`: a global the legacy file expands is read as a scan with its expansion and shot count; a global with none, and a group written before expansions were stored, have no scans. **keep**.
- `test_a_converted_file_holds_the_globals_the_legacy_file_did`: converting a legacy file to TOML keeps every expression, unit, expansion and scan flag. **keep**. It compares the converted file against the legacy read, so it catches conversion losses and leaves reading errors to the test above (each mutation fails only its own test).

### tests/test_new_globals_file_dialog.py

The save panel behind *New globals file*; only the OS panel is replaced, and what is read back is the name it was offered and what the handler does with the answer. The macOS panel invents an extension for a bare folder, so the name offered must carry `.toml`.
- `test_the_offered_filename_carries_the_toml_extension`: the panel is offered a file name ending in `.toml`, so it keeps the extension and only the stem is typed over. **keep**.
- `test_the_offered_filename_sits_in_the_last_opened_folder`: the panel opens in the folder globals files were last opened from. **keep**.
- `test_a_name_returned_without_the_extension_is_given_one`: a name returned without `.toml` still makes a `.toml` globals file, which is written and opened. **keep**.
- `test_a_name_returned_with_the_extension_keeps_it_as_it_is`: a name that already ends in `.toml` is not given a second one. **keep**.

### tests/test_send_to_runviewer.py

`send_to_runviewer` through the real `RunviewerClient` to a real `RunviewerServer`; only the process start is faked.
- `test_a_shot_reaches_a_running_runviewer_as_its_local_path`: with runviewer running, the shot reaches it as its local path and no second runviewer is started. **keep**.
- `test_runviewer_is_started_when_nothing_is_listening`: with nothing listening, exactly one runviewer is started and then given the shot. **keep**. `assertIn('runviewer', self.launched[0])` matches an argv element, so it pins the `python -m runviewer` form of the launch; a different launch command for runviewer would need that line loosened.
- `test_the_operator_is_told_when_runviewer_cannot_take_the_shot`: when runviewer refuses the shot, its own reason reaches the output box. **keep**.

### tests/test_sequence_attrs.py

The sequence a default shot belongs to.
- `test_every_default_shot_of_a_day_is_in_one_sequence`: default shots made at different times of one day share one sequence id, have sequence index -1, and claim no sequence index. **keep**.
- `test_default_shots_named_by_a_global_are_numbered_through_a_restart`: default shots count 0, 1, 2 as run numbers, written into the shot files with the sequence's attributes, even when the day's file names differ by a global and runmanager is restarted between them. **keep**.

### tests/test_value_column.py

The foldable Default and Scan expressions in a group tab: a real `GroupTab` in a real tree view, rendered offscreen, driven by mouse and key events.
- `test_native_fold_shows_two_distinct_editable_expressions`: the inactive expression sits folded under its global, shows when the row is unfolded, and each expression is edited and saved on its own. **rename**. "Native fold" says how, not what. Proposed: `test_the_second_expression_unfolds_under_its_global_and_each_expression_edits_on_its_own`.
- `test_rendered_label_and_text_follow_active_expression`: the value cell draws a label and text that change when Scan is ticked (the label reads default or scan). **keep**.
- `test_toggling_scan_commits_editor_and_swaps_active_expression`: ticking Scan commits a half-typed default first, then makes the scan expression the active one; unticking returns to the default. **fix**. The first line binds `parent` and never reads it (found by an AST scan for names assigned and never read; its other hits are the intentional `_qapplication` holders and the `labscript_utils.h5_lock` side-effect imports); use `_`.
- `test_remote_toggle_preserves_other_globals_uncommitted_editor`: a remote change of another global's Scan or JIT leaves the half-typed editor of the global being edited open and uncommitted. **keep**.
- `test_ticking_scan_with_no_scan_opens_its_line_for_typing`: ticking Scan on a global with no scan expression opens the scan line for typing, and what is typed is saved. **keep**.
- `test_keyboard_and_copy_follow_selected_expression`: typing on a selected expression edits that one and Copy copies the selected one, default or scan. **keep**.
- `test_checkbox_cells_toggle_and_shift_click_preserves_selection`: a click anywhere in a Scan or JIT cell toggles it, and a shift-click does not toggle it. **rename**. The test never asserts the selection, which is Qt's own: it asserts only that the shift-click leaves the box alone. Proposed: `test_a_click_in_a_checkbox_cell_toggles_it_and_a_shift_click_does_not`.
- `test_child_selection_targets_the_global_for_bulk_changes`: a bulk change applies to the global when its folded child row is the one selected. **keep**.
- `test_metadata_refresh_keeps_uncommitted_editor_text`: a refresh of an expression's display (backgrounds, tooltip) does not replace text the user is typing. **keep**. Not the same guard as the remote-toggle test: removing it fails this test and leaves that one passing.
- `test_expansion_selection_and_sort_survive_scan_toggle`: ticking Scan keeps the global unfolded, selected and current, and re-sorts the list on the new value. **keep**.
- `test_arrow_keys_visit_expanded_child`: Down from an unfolded global's value goes to its folded child, not to the next global. **keep**. Not a Qt fact: `ItemView.moveCursor` moves flat, and `GlobalsTreeView.moveCursor` is what lets Down enter the child (mutated, exit 1).
- `test_tab_from_active_value_goes_to_units`: Tab from the value editor commits and moves on to Units, not down a row. **keep**. Qt's own Tab in a tree moves down a row, so this is `GlobalsTreeView.moveCursor` (mutated, exit 1).
- `test_hidden_unicode_in_inactive_expression_stays_visible`: an expression with a hidden character in the folded line forces the fold open and cannot be collapsed. **keep**.

#### Totals (eleven files)

- Test files: 11 (and `fixtures.py`, `conftest.py` as context). Tests: 38, with 6 subtests.
- Marks: keep 31, rename 3, fix 4, cut 0.
- rename: `test_queued_shots_come_back_at_startup_and_a_later_load_leaves_them`, `test_native_fold_shows_two_distinct_editable_expressions`, `test_checkbox_cells_toggle_and_shift_click_preserves_selection`.
- fix: `test_each_shot_file_is_reported_as_sent_rejected_or_not_sent` (pins two message texts), `test_a_restart_during_a_compile_does_not_wedge_later_compiles` (cannot fail without its fake reply), `test_a_scanned_value_compiles_back_to_exactly_what_was_frozen` (vacuous loop), `test_toggling_scan_commits_editor_and_swaps_active_expression` (dead `parent`).

#### Evidence (eleven files)

Scratch copy: `git archive CodeHealth` extracted to `.../scratchpad/contract_other/copy`, with `copy/runmanager/__init__.py` confirmed as the imported package. One line broken at a time by `.../contract_other/mut.py`, which restores the file; each test then run alone from inside the copy: `cd copy && LABSCRIPT_NO_ERROR_DIALOG=1 ~/miniforge3/envs/labscript/bin/python -m pytest tests/<file> -q -p no:cacheprovider -k <name>`. Exit status read directly. Baseline before any mutation: 38 passed. After the mutations the copy was diffed against a fresh `git archive` and was identical. Exit 1 = the test failed (caught); exit 0 = it still passed.

Backing the fix and rename marks:
- `test_a_scanned_value_compiles_back_to_exactly_what_was_frozen`: `get_shots_with_frozen_globals` returning `[]` for its shots, and returning only `shots[:1]`: both exit 0, so it cannot fail on them. Plain `repr`, and dropping `threshold=sys.maxsize`: both exit 1.
- `test_a_restart_during_a_compile_does_not_wedge_later_compiles`: the `from_child.put(['done', False, ...])` fake reply deleted from `on_restart_subprocess_clicked`: exit 0, so it cannot fail on that. The prototype with the old child kept hung (scratch copy only) passes on real code and exits 1 on that same mutation. Exit 1 for: `self.child_ready.clear()` removed; `to_child.put(['quit'...])` removed; `self.child_ready.set()` after the spawn removed.
- `test_each_shot_file_is_reported_as_sent_rejected_or_not_sent`: fails on a harmless rewording, exit 1 with `'Analysis is off.'` changed to another sentence (subtest "Analyse is off"), and with `'Cleared before it was sent to lyse.'` changed (subtest "cleared while lyse is away"). Relaying lyse's reason replaced by a fixed word also exits 1, so that assertion stays.
- `test_checkbox_cells_toggle_and_shift_click_preserves_selection`: shift-click toggling exits 1, and a click that does not toggle exits 1, so the toggle half is enforced. The selection half is not asserted anywhere in the test (read from the code, not mutated).

Mutations that fail the keeps (all exit 1):
- analysis_submission: a rejected shot also kept for retry (`success = False`) fails `test_a_shot_lyse_refuses_is_dropped_not_retried`; a connection failure counted as delivered fails `test_a_shot_waits_while_lyse_is_away_...`. `test_a_submitted_shot_reaches_lyse_as_its_local_path` was not mutated: its one assertion is the path arriving through `add_shot`.
- blacs_status: `path_to_local` removed; override note removed; `shot_path` branch disabled; error left out of the state; `requesting_shots` ignored. Each fails its own test.
- new globals file dialog: suggested name `untitled`; suggested folder `~`; extension guard removed; guard replaced by always append. Each fails its own test.
- send_to_runviewer: `add_shot` removed; `say_hello` always failing (second server on the port); launch command changed to `-m blacs`; launch replaced by a no-op; the add_shot error not shown. Each fails its own test. A mutation that only changed `if os.name == 'nt'` to `if False` is equivalent on POSIX and was not counted.
- sequence_attrs: not dated from the start of the day; default shot claiming a sequence index; run count forgotten after a restart (`start = 0`); run number always 0. Each fails its own test.
- globals_file: lock removed from `_locked`, three runs, each failed with a stale value.
- configuration: rows restored on every load; rows never restored; queue settings applied only at startup. Each fails.
- legacy: `scan_enabled` no longer derived from the expansion fails only `test_a_global_the_file_expands_arrives_as_a_scan`; the converter dropping units fails only `test_a_converted_file_holds_...`; the legacy guard removed from the last loop of `guess_expansion_modes` fails the stores-nothing test.
- value column, all 13 mutated: scan child not editable (1); delegate label constant, and expression text not painted (2); no commit on swap (3); swap commits any open editor (4); no `edit_cell` on tick (5); `AnyKeyPressed` removed, and Copy copying nothing (6); click not toggling (7); child selection not mapped to its global (8); `setEditorData` guard removed (9); no re-sort after a tick (10); `GlobalsTreeView.moveCursor` always flat (11); Tab as Qt's native next row (12); auto-expand removed, and collapse allowed (13).

Not done: the three stand-ins (lyse window, compile child, OS save panel) were not replaced by real ones. The lyse window and the panel cannot run in a test. The compile child could be a real `batch_compiler` process, but the test is about the restart's queue handling, not compiling.

## Step 2.1: behaviour no one asked for

The source is `git diff master CodeHealth`: 338 commits, merges included, and
runmanager's package and docs +6411 −2077 lines. Line references are to
`72baf75`, whose sources are those of `57d3d21`. Almost every behaviour the fork
added traces to a request, to Ian's plan for the queue, or to a contract he
agreed. The March work (fa0b98c..0f55afe) has bare commit subjects; its record
is labscript-suite's `RunmanagerQueue.md` (8fcb362, 2026-03-17, deleted in
45eb515) and the branch entries in `CODEX.md`.

| Behaviour | Commits | Where it was asked for |
| --- | --- | --- |
| TOML globals files with a Default and a Scan field, Scan? and JIT? per global, Expansion only with Scan?; HDF5 globals read as an import and converted to TOML at the first edit; unknown keys kept | fa0b98c, deeffe4, 1bc4313, bf675b5, 9c2942e | `RunmanagerQueue.md` steps 3 and 4; `RunmanagerTOMLGlobals` in `CODEX.md`. JIT? comes from `LAZY_COMPILE.md` (deeffe4) and was named by Ian on 2026-09-29; unknown keys kept, Ian (memory `runmanager-toml-rules`) |
| An HDF5 file's expanded globals arrive as scans; no expansion guess is stored in one; a remote set of one is refused; one warning per file | f76fca0, 464ea2a, 7a84b00, 0df67ea | Bugs found on 2026-09-25, hotfixed onto Production (dc824d6, a367fdd); the 2026-09-29 review; the campaign's legacy-file warning. 464ea2a overrides the plan's "Legacy HDF5 scan semantics are intentionally not preserved" |
| One Value(s) column, the expression not in use folded below it; cell colours from the theme | 96f4dd7, ee14b7e, 3a2188d, e4a440f, 831d384, 94c7f95, 5c54e64, 5bb5ba5 | `GlobalsValueColumn` in `CODEX.md`, a prototype for Ian to try in the lab |
| Remote getters and setters named after the field they touch, `get_globals` as what runs, a refused batch writing nothing, `set_globals` gone | 1bc4313, ae80fee, 6258451, 5a766bc, b6b9a7f, c7bf121, 8b06041, 4cd262d, 4e89cd8 | Ian's M1 ruling, 2026-09-29 (memory `runmanager-value-and-scan-fields`); `PreexistingReviewFixes` in `CODEX.md` |
| The queue in runmanager: its tab, eager and lazy compile, the empty-queue policy and default-shot file, Delete by key and menu, Clear deleting files, the queue saved with the front panel and restored only at startup, uncompiled eager rows compiled after a restore | 208d130, 9631c64, deeffe4, 72d5c12, 7fb935b, 8d0ee0d, 41c22cf, 35c0bdc, af2bc10, da162e9, 4e2a0fa, de97960, 57198bb | `RunmanagerQueue.md` step 5; `RunmanagerQueue` and `RunmanagerQueueSimple` in `CODEX.md`; the 2026-08-28 review's slices (meta `issues/runmanager_issues_2026_08_28.md`, 6a22e24); Ian's M2 ruling (memory `runmanager-queue-saved-with-front-panel`) |
| Engage's hold menu of four submission modes, greyed with no last sequence | 51d3bc3, 30eec60, 9c84e0c, 0759ac0, bc93c37, aaff64a, ada06a0 | `RunmanagerQueueSubmissionTypes` in `CODEX.md`; the names from `AgentEngage` |
| "Add shots to last sequence" always joins; the sequence record keyed by id and index; a join refused for an unknown sequence or another labscript file; a replacement numbered around shots still compiling; joined shots named from their sequence's formats | a616e5c, 94b90db, 60cce11, 73a15d6, c5888b3, 37c388b, d779c9d, d08af64, 8e52d6d, 8f4f289, a56169f, 2827765, 3427363, 7cedeec, fc3372b | Ian, 2026-10-08 (memory `runmanager-add-to-last-sequence-always-joins`); the 2026-09-24 review's slices 2, 3, 4, 9 and 16 (runmanager's `issues/runmanager_issues_2026_09_24.md` at 66aec0d), the HITL ones decided by Ian |
| Output-folder and filename-prefix templates read globals; braces kept; two formatting passes | c29bfba, 0f55afe, 506caf2, 2d41314, cb35865, 2e19640, 523ad2c | `RunmanagerGlobalFormatStrings` in `CODEX.md`; Ian, 2026-09-29 and 10-11 (memory `runmanager-shot-names-no-braces`) |
| The BLACS handover: one exchange, a stable `shot_id` per row, the reserved first row, red failed and rejected rows, Pause queue, Delete cancelling a running row, the reclaim, repeated outcomes harmless, errors answered rather than raised, unreadable outcomes refused, a completion with no row still sent to lyse, pending while the head compiles, BLACS's status beside its checkbox | 03ee4d9..83179fc, 0e8f1ce, 1c06af0 | `RunmanagerControl` in `CODEX.md`; blacs's `docs/source/shot-management.rst`, the contract of record |
| Default shots as ordinary rows of the day's default sequence (index −1), numbered within it, left out of a saved queue, dropped after a globals change, a failed one's file removed | 9631c64, 265fcd3, 6499c12, 7ca1ee1, f54ea89, c4a668b, 6550507 | `RunmanagerQueue.md`'s standard shot; `shot-management.rst`, "Default shots"; the 2026-09-24 review's slice 10; meta 283c329 |
| Each shot's id written into its file, `submit_shots`, `shot_status`, `n_runs` as the sequence's reach when a file is written, a failed preparse releasing its waiting callers | d3e5f08, 29f798a, daa0274, 4178be4, 453c949, 9b92884, 0687152, b9409da, cd81188, c1a3862 | `labscript-optimization` in `CODEX.md`, which submits with `submit_shots`; the 2026-09-24 review of that work |
| Remote Engage by submission mode with receipts, the lyse toggle, `get_queue`, `test_compile` into a scratch file, the compile traceback on the child's reply, one record per shot with its compile, queue, BLACS and lyse steps, a lyse clear between files | 19db018, ccb16df, 1834a02, c5910ea, a55a688, a336cd0, 2e77c15, 0d5fc2b | meta `issues/runmanager_agent_engage_2026_10_06.md`; `AgentEngage` in `CODEX.md` |
| Remote expansions, units, groups, and creation and deletion of globals; a temp file per save and a lock per edit; an ordered axis the globals no longer produce skipped; a shot that fails to compile leaving the queue | f0024a6, e6702ef, 00ea2a4, 5d5cf4d | The lab's list (T1–T4, T13–T15), named in merge 5695de2; labscript-agents' `Development` uses the calls (4807785, f25f7d0, 1cdb02d) |
| lyse submission in runmanager: its row, its retries, refusals not retried | 208d130, 9846f69, b41de54 | `RunmanagerQueue.md` step 9; memory `lyse-submission-decoupled-from-acquisition` |
| `RunmanagerServer` and `runmanager.client` on labscript-utils' base, `remote.py` building the default client; BLACS, lyse and runviewer reached through their clients | 59a0dab, 7688d1d, 9a73b66, 7e9ebd0, 01a5cd9, a68cda8, c83fba5 | meta `issues/server_interfaces_2026_09_25.md` |
| The BLACS, lyse and runviewer lights; the destination checkboxes named for their applications, with logos; the window rearranged; a client per light and per runviewer send | f29274a..952216f, 98f8eb8 | `LinkCheck` in `CODEX.md` |
| Files opened in the labconfig's editor; no `[programs]` keys required | 49f5ffb, a8fcadb | `EditFile` in `CODEX.md` |
| TOML configuration, its path in the window title, macOS names | 552374d, 7f2fdb0, 6d04b41, ceb5051, 570e029 | `toml`, `UpdatedTitleBars` and `MacOS` in `CODEX.md` |
| PyQt6 and Qt6's removed APIs; upstream's 2026 work recovered; the Shuffle boxes' check states | 1d9a17f, 36f1e03, 811a3ef, 514fad9, a09dead, d938f95, b835297, 5dd27dd, 9754120, 9f7e270, 3bb19f3, 11d2867, 83b6fa2 | The suite's move to PyQt6; `UpstreamRecovery` and `PreexistingReviewFixes` (L14) in `CODEX.md` |
| runviewer started without `os.fork`; compiles held while the compiler restarts; Empty queue stopping a batch compiled for runviewer alone; each shot keeping its View shot(s) choice; Engage with neither box ticked compiling; `get_labscript_file` answering `''` | 9ba9c52, 0fea5cd, 796634d, 5765d7e, 4fb0c60, 1dac815, 8fb99f6, 9a7b0e4, 1362cef | Bugs: the RunmanagerControl operator pass (`CODEX.md`), the 2026-09-29 review, and the mapping pass (meta fa91d9f) |
| Shared code, version scheme, docs and the campaign's earlier rounds, among them `LabscriptGuiApplication`'s save prompt and window geometry, which upstream runmanager did not save | 5db8175, 538ce6c, 9877b45, 26a7777, 74457a7, 20ee516, 169056a, 16857fd, 3dcdfea, e3e03ac, 6e80ffb, e21a5f2, bdf128f | The campaign's approved rounds; the geometry came with blacs's D1 |
| Test environment and test rules | 1729743, ad0ab10, c8a1a26, 7b44155, 4335491, 3dd56d0, 65991aa, 9c36351..72baf75 | The workspace's test rules and Ian's rule 13 |

Not candidates:

- The handover's guards are stated in `shot-management.rst`. The one line it
  does not state, the red output line when a running row is offered again,
  the 2026-08-28 review's slice 3a asked for ("announced").
- A bare path in a saved queue becomes a compiled eager row
  (`queueing.py:279-280`): Production saves its queue as bare paths
  (`Production:runmanager/queueing.py:175-181`), so this restores a lab's
  configuration.
- The remote setters' type checks (`__main__.py:4997-5008`): PyQt6's
  `setChecked` already refuses a str, None or numpy bool (checked offscreen),
  and without them `set_scan_enabled({'x': 'False'})` would store True.
- The refusal of a remote set of an HDF5 globals file (7a84b00): the Convert
  dialog would otherwise hold the server.
- The refusal of a remote group activation that leaves two active groups of
  one name (8ee58d8): Ian asked for it on 2026-10-27, through the coordinator,
  after the lab's remote calls. It refuses at the remote boundary; the
  preparse's dialog itself waits for the suite-wide notification bar (item 21
  of the 2026-10-07 review).
- Fixes of faults the reviews reproduced, such as compiles held during a
  restart and a replacement never taking a compiling shot's name.
- For step 4: `notify_shot_complete`'s `return 'queued'`
  (`analysis_submission.py:160`), which nothing reads; `prepare_queue_shot`'s
  `active_groups is None` (`__main__.py:4583`), which
  `get_active_groups(interactive=False)` never returns; the `None` checks at
  `__main__.py:2482` and `:2488`; `get_queue_paths()`'s default
  `include_default_shots=True`, used only by tests; `remote.py`'s demo block
  (`:39-47`); the upstream functions taken out of `__init__.py`, and
  `set_globals` (b083388, 610f9a4, 4cd262d). For step 5:
  `docs/source/index.rst:31` still links the BitBucket archive.

### Candidates

| | Behaviour | Cost | Recommendation |
| --- | --- | --- | --- |
| A | An expression holding an invisible or control character gets an icon and a tooltip line, and its row refuses to collapse while the expression not in use holds one (`__main__.py:221-243`, `:1536-1537`, `:1555-1574`, `:1008`). It came with the TOML commit fa0b98c; no request was found, and `RunmanagerQueue.md` does not name it. Python already refuses such a character outside a string literal, and that error shows in the cell's tooltip, so the warning adds string contents and the expression not in use | about 35 lines, one test | keep the icon and tooltip; drop the refusal to collapse (`on_global_collapsed`, 8 lines) |
| B | The globals view's Copy shortcut, `on_globals_copy` (`__main__.py:1022-1028`, `:1220-1224`), from the value-column review (ee14b7e), when one cell drew two expressions. Since the tree (e4a440f) it copies the current cell's text, which Qt's item view does itself on the Copy key: shown offscreen on a plain `QTreeView` | 12 lines; one test calls the method directly | cut; the test sends the Copy key instead |
| C | `ItemView.moveCursor` steps past hidden columns (`__main__.py:528-532`), added in ee14b7e for the value column's hidden Scan column. e4a440f removed that column, and no runmanager view hides one now | 6 lines | cut, back to upstream's `sibling` |
| D | `new_sequence_details(format_globals=)` (`__init__.py:593`, `:639`), added for default shots (0f55afe). Its last caller went in cb35865, and no repo passes it | 3 lines | cut |
| E | `runmanager.remote.queue_exchange` (`remote.py:37`, 03ee4d9), BLACS's exchange as a flat function for user code. No repo's history has called it: BLACS used `remote.Client`, then `RunmanagerClient` (blacs 47aefd6). `shot-management.rst` says a request for work from anywhere but BLACS's shot loop hands one shot out twice | 1 line | cut |
| F | `skip_missing` on `set_scans` and `set_expansions` (2e77c15, f0024a6). labscript-optimization passes it only to `set_values`, and labscript-agents, `CodeHealth` and `Development`, passes it to none | about 10 lines in client and server | keep, as one signature for the three setters |
| G | `submit_shots` with no entries answers `[]` (453c949, `__main__.py:5390-5394`). labscript-optimization returns before submitting when it has nothing, and it is the only caller; without the check the server raises `IndexError` | 5 lines, one test | keep |
| H | An unknown compile mode or empty-queue policy, in a saved queue or on a row, is replaced silently by the default (deeffe4, `queueing.py:294-295`, `:674-678`, `:687-688`). runmanager has only ever written `eager`/`lazy` and `nothing`/`default_labscript`, and Production saves neither setting, so only a hand-edited file holds another value | 9 lines | cut |
| I | A default shot that real work got in front of is dropped, with its file, at the next offer (`drop_overtaken_default_shots`, ecc4262, `queueing.py:709-736`, `:1169-1171`). It guards a race the RunmanagerControl review found between deciding that the queue is empty and enqueueing the default row. `shot-management.rst` does not state it, and no record names it | about 30 lines, one test | keep; step 2.3 traces the race to its cause |

Searched: `CODEX.md` and the meta repo's `issues/`, with their history
(`git log -p`), including the deleted 2026-08-28 slices; runmanager's `issues/`
history (the 2026-09-24 slices at 66aec0d), `DEEP_PASS.md`, `LAZY_COMPILE.md`
and docs; labscript-suite's deleted `RunmanagerQueue.md`; blacs's
`shot-management.rst`; shared memory; and each commit's message and branch.
Runmanager's 2026-09-29 review is in no repo; its labels (H3, M1, M2, M8, L9,
L12, L14) survive in commit messages, `CODEX.md` and memory. The lab's T-list
is named only in 5695de2. Callers: `git grep` over every repo in the workspace,
hyde and the meta repo included, tests included, at `CodeHealth` (else `main`
or `Production`), and at labscript-agents' and labscript-optimization's
`Development`, each beside a pattern that matched. Users reached: the operator
through the window; BLACS through `queue_exchange`, `get_status` and its
integration tests; lyse through `LyseClient.add_shot`; runviewer through
`RunviewerClient`; labscript-optimization and labscript-agents through
`RunmanagerClient`; labscript scripts through their globals and shot-file
attributes; labscript-devices' FunctionRunner through `runmanager.remote`,
blacs's connection-table plugin through `globals_file`, and labscript-utils'
profile through `compile_labscript_async`. Of labs' own code, spielman-devices
imports nothing from runmanager; lab scripts and userlib are not on this
machine and could not be searched. The two probes ran PyQt6 alone, importing
nothing of runmanager.

### Bugs found

1. **fix**, both tiers. `__main__.py:4493-4503`: the last of
   `guess_expansion_modes`' three expansion writes, which clears a zip group
   from a global that is not iterable, has no legacy-HDF5 skip. f76fca0 gave
   the other two one, reasoning that an HDF5 import carries no expansion, and
   464ea2a made it carry them the same day. So an HDF5 global in a zip group
   whose value is not iterable, such as a dependent of a TOML global changed
   from a list to a number, raises `LegacyGlobalsFileError` on every preparse:
   an error window each time, no evaluation in the tabs, and Engage left
   disabled. Production has the same write (`__main__.py:3884-3891`). Found by
   reading, not run. The fix is the other two's skip.
2. **fix**. `__main__.py:2457-2466`: `on_close_event` does not call
   `discard_default_shot`, so a default shot prepared but not yet collected
   stays in the day's default folder as a shot nobody ran. Open in meta
   `issues/runmanager_issues_2026_08_28.md`, whose entry goes with the fix.
3. **fix**, Ian's decision; otherwise **document**. A default shot that
   cannot be made is attempted again on every BLACS request. With the
   default-shot file gone, `offer_shot` raises (`__main__.py:4908-4911`) and
   each exchange writes a red line, about once a second while BLACS has no
   local override shot. With a default script that fails to compile,
   `take_default_shot` (`:4663`) starts a new compile on each request: a
   traceback each time, and the compiler kept busy ahead of Engage's shots.
   f54ea89 removed the files this left, not the repetition. A queued shot that
   fails to compile is not retried (5d5cf4d).
4. **fix**. `LAZY_COMPILE.md:39-45` says a failed compile leaves its row red at
   the head, to be recompiled with *Compile again*. Since 5d5cf4d the shot
   leaves the queue, and there is no Compile again.
5. **fix**, by blacs's owner; done in blacs 9528b18, on its `CodeHealth`. blacs `docs/source/shot-management.rst:112-119`
   and `:219-221` say the same of a shot that fails to compile. runmanager's
   `usage.rst`, `client.py` and labscript-agents (1cdb02d) already say it
   leaves the queue.
6. **fix**, the wording Ian's. `analysis_submission.py:1-13` reads "Copyright
   2026, JQI, Author: Ian Spielman" (bdf128f, replacing "Copyright 2026, Monash
   University"). The module is BLACS's upstream `analysis_submission.py`,
   moved: its mainloop, waiting list, `check/retry` signal and
   `update_waiting_files_message` are upstream's, under "Copyright 2013, Monash
   University", a notice the BSD licence requires kept.
7. **document**, Ian's decision; the docstrings say so in bb2a57f. `globals_file.py:92-93` turns an
   empty expansion on a global with Scan? ticked into `outer` on every save. So
   `set_expansions({'x': ''})` on such a global answers as though `''` were
   stored, against `client.py:176-177` ("`''` for none"), and clearing the
   window's Expansion cell comes back as `outer` after the preparse.

### Decided and applied

Ian kept F, G and I, and had A–E and H cut as recommended. He had bugs 1, 2, 4
and 6 fixed, bug 5 by blacs's owner (blacs 9528b18), and bugs 3 and 7
documented, the behaviour left. Bug 3 is in `KNOWN_ISSUES.md`: its fix, a
failure remembered until its source changed, was reverted after two review
rounds found faults in that memory. For bug 6 the file has upstream BLACS's
banner again.

Range for review: `7ede9b5..86de3e4` on `CodeHealth`, one commit each: A
29de04e, B fa97b78, C 7915026, D 319a7ab, E 6ab5242, H 2cebb82, bug 2 cd5b36d,
bug 4 c8171f8, bug 6 1f96b72, bug 7 bb2a57f. Bug 1 is on `Development` as 07058b7,
with its test 52466dc, and on `Production` as d475d27, adapted to its helpers; it
reached `CodeHealth` in the merge 7ede9b5, with `LabAgentFixes2`. Bug 2's meta
issue is closed (meta 376dc32). The suite is 146 tests, with 27 subtests, and each
new or changed test fails without its change.

From Codex's reviews, agreed by Ian: H's unknown settings, the queue's and each
row's, are replaced again and reported in red (0e9fa47, 888141b); the
hidden-Unicode test collapses the row and reads the tooltip (c1caa88); and the
default shot a close during its preparation leaves is in `KNOWN_ISSUES.md`
(5ab9f2f), since waiting for it would deadlock on the preparation's `inmain`.
Bug 3's fix (a1d5d85, fd02976 and its test 5f3107b) is reverted, in 5773cf7,
4c199fb and e2b0d13, and the retries are in `KNOWN_ISSUES.md`. Each new test
fails without its change.

## Step 2.2: duplication

Searched: runmanager's package line by line for jobs done twice; every name runmanager
shares a concept with in blacs, lyse, runviewer, labscript-utils, labscript, zprocess,
labscript-agents and labscript-optimization, read at their `CodeHealth` refs and at
`master` for upstream; and the librarian's map, with its answer on copies in other
repos. Line references are to `1e80564`. **fix** marks a change of a few lines inside
the existing structure; everything else is for Ian.

### Candidates: jobs done twice

| | Job | Copies | Should it exist | Its one copy | Size |
| --- | --- | --- | --- | --- | --- |
| D1 | Remote write of one field of many globals | `RunmanagerServer._set_expression_field_values` (`__main__.py:5066-5099`) and `_set_field_values` (`:5101-5132`): 26 of 34 lines the same; they differ in the coercion, the raw check, `repr` and the kept trailing comment | Yes, the public setters | `_set_field_values`, given the per-value preparation. **fix** | −26 +8 |
| D2 | Commit one edited field of a global, from the window or a remote setter | `GroupTab.change_global_default`, `_units`, `_scan_enabled`, `_jit_enabled`, `_scan`, `_expansion` (`__main__.py:1623-1900`, 278 lines): one skeleton (set the text, make the file editable, `set_field`, revert and report on failure, record the previous value, sort, `globals_changed`). Upstream had three; the fork added the others | Yes | One `GroupTab` method taking the field and a step to run after success | −120; the six methods rewritten |
| D3 | The name of the global a model item belongs to | 8 copies of 3–4 lines in two forms (`__main__.py:1185`, `1239`, `1249`, `1258`, `1267`, `1276`, `1286`, `1330`); the six `on_globals_model_*_changed` handlers (`:1236-1282`) share the rest of their body too | Yes | One `GroupTab` method using the form that also handles a folded row. **fix** | −15 |
| D4 | Groups-tab model walks | "Selected group rows" written out four times (`__main__.py:3095`, `3115`, `3133`, `3155`), with two variants (`:3105`, `:3167`); walks over `groups_model` in `get_active_groups` (`:3736`), `get_save_data` (`:4093`) and `RunmanagerServer._group_active_items` (`:5054`); a new group's row added in `new_group` (`:4006`) and `copy_group` (`:3961`) | Yes | Two or three `RunManager` methods | −45 |
| D5 | The file name of shot N of a sequence | `make_run_files` (`__init__.py:712-720`), `reindex_run_file_infos` (`__main__.py:2711-2730`, half by the shared utility, half by hand) and `prepare_default_shot` (`:4709-4729`); the prefix formatted and joined to its folder three times | Yes: a new, a joined and a default sequence | One function beside `make_run_files`, over `next_available_indexed_filepath` with a keyword that keeps the stem's digits (bug B5) | ~30, with a backward-compatible keyword in labscript-utils |
| D6 | Starting runviewer detached | `send_to_runviewer` (`__main__.py:4609-4635`, 27 lines); `zprocess.start_daemon`, already used by labscript-utils and lyse, does it with a short-lived launcher that is safe on macOS | The launch, yes; this copy, no | `start_daemon`, given desktop-app's `runviewer-gui` on Windows and `python -m runviewer` elsewhere. On Windows the flags become `CREATE_NEW_PROCESS_GROUP \| CREATE_NO_WINDOW`, unchecked on a Windows machine. `test_send_to_runviewer.py` fakes `start_daemon` instead. **fix** | −22 +3 |
| D7 | Talking to the compiler subprocess | The `['compile', ...]` request and `('done', ok, error)` reply in `compile_labscript_async` (`__init__.py:840-850`), `compile_multishot_async` (`:883-891`) and `compile_run_file` (`__main__.py:4274-4283`), whose comment says "change all three"; the subprocess started four times (`__init__.py:837`, `878`, `__main__.py:2078`, `2978`) | Yes; both async functions are upstream's, used by labscript-utils and blacs | Two small functions in `__init__.py`, each caller keeping its callbacks and errors. **fix** | ~+10 −20 |
| D8 | Evaluating globals into exactly one shot | Upstream's `make_run_file_from_globals_files` (`__init__.py:790-803`) does what the fork's `get_queue_compile_globals` (`:272-291`) does | Yes; blacs's compile-and-restart uses the first | `get_queue_compile_globals`, taking the first's refusal, which names the scanning globals. **fix** | −8 |
| D9 | A `[runmanager]` format with its fallback | `get_output_folder_format` and `get_filename_prefix_format` (`__init__.py:559-594`, fork's, used only by `new_sequence_details`): the same try/except and `sequence_index` substitution | One private function, using `LabConfig.get(..., fallback=)` | **fix** | −14 |
| D10 | Name checks | Upstream's `is_valid_python_identifier` (`__init__.py:47-63`, a tokenizer) and `is_valid_hdf5_group_name` (`:66-89`, a loop over code points): `str.isidentifier` and `str.isascii` do these jobs | The names, yes (upstream's); the bodies, no | One line each; fixes bug B1. **fix** | −25 |
| D11 | Scan? and JIT? excluding each other | `globals_file.set_field`'s `scan_enabled` branch (`globals_file.py:398-414`) does again what `_normalise_record` (`:74-94`) does on the save that follows; checked over 96 cases. The `jit_enabled` branch is needed | No | `_normalise_record`. **fix** | 17 → 4 lines |
| D12 | Deleting a shot's file | `discard_default_shot` (`__main__.py:4657`) and `prepare_default_shot` (`:4752`) ignore every `OSError`; `QueueManager._delete_queue_files` (`queueing.py:1107`) ignores a missing file and reports the rest | Yes | `QueueManager`'s, so a default shot's file that cannot be deleted is reported too. **fix** | −8 +2 |
| D13 | `remote`'s default client | `remote.py:6` builds `RunmanagerClient()`; `ZMQClient.instance()` is the suite's per-class singleton | Yes | `RunmanagerClient.instance()`. **fix** | ±1 |
| D14 | A shot's full status record | `QueueController._status` (`queueing.py:453`) and `track_unqueued` (`:519`) both build it | Yes | `_status` | −9; marginal, leave |

### Candidates: one concept, two spellings

| | Concept | Spellings | Upstream or majority | Proposal | Size |
| --- | --- | --- | --- | --- | --- |
| N1 | `runmanager.remote`'s functions | 28 of the client's 44 methods; upstream's `remote.py` had every method. Missing: `get_expansions`, `set_expansions`, `get_units`, `get_groups`, `set_groups_active`, `new_globals_file`, `open_globals_file`, `close_globals_file`, `new_group`, `new_globals`, `delete_global`, `get_analyse_shots`, `set_analyse_shots`, `get_queue`, `test_compile`, `queue_exchange` | Every method | Add the 16. **fix** | +16 |
| N2 | A component's saved state | `AnalysisSubmission.get_configuration_data` and `restore_configuration_data` (`analysis_submission.py:96`, `99`); upstream's BLACS class, which it is, and every other component in the suite say `get_save_data` and `restore_save_data` | `get_save_data`, `restore_save_data` | Rename back. **fix** | ±5 |
| N3 | The BLACS flag | `send_to_BLACS` (`__main__.py` ×9, `queueing.py` ×3, tests ×6) beside `send_to_blacs` (`__main__.py:4123`, `4142`, `4220`, the saved key) | Both upstream's; snake_case is the rule | `send_to_blacs` | ±21, runmanager only |
| N4 | Globals file, group and global | `globals_file.py` (the fork's) and five upstream functions in `__init__.py` say `filename`, `groupname`, `globalname`; `__main__.py` and `client.py` say `globals_file`, `group_name`, `global_name` (~240, 217 and 279 uses); `client.py`'s globals-file methods say `path` | `globals_file`, `group_name`, `global_name` | `globals_file.py` and the client's `path` follow the majority; upstream's `__init__.py` functions keep theirs. No caller in any repo passes these by keyword | ~90 lines in runmanager; `path` also ~15 in labscript-agents, whose tool schema names it |
| N5 | A sequence in `submit_shots` | `sequence` (`client.py:539`, `__main__.py:5402`) against `sequence_id` everywhere else | `sequence_id` | Rename; labscript-optimization passes it by keyword (`runmanager_interface.py:88`) | ~15 in 2 repos |
| N6 | The queue widget's rows | `RunmanagerQueueWidget.set_queue_paths(paths)` takes row dicts (`queueing.py:218`), where its base class says `set_row_infos(row_infos)`; `QueueManager.delete_rows(rows)` against `QueueController.delete_rows(shot_ids)` | The base class's | Rename. **fix** | ±14 |
| N7 | The queue's saved state | `QueueController.export_state` and `restore_state` against `get_save_data` and `restore_save_data` elsewhere; upstream BLACS's queue said the latter | `get_save_data`, `restore_save_data` | Rename; blacs's integration test calls them | ~30 in 5 files, blacs's tests among them |
| N8 | A shot's run number | `run_no` inside runmanager (upstream's) against `run_number` in the receipt and `submit_shots` (public to labscript-agents), and `nruns` beside `n_runs` in `make_run_files` | `run_no`, `n_runs` | `nruns` → `n_runs` (**fix**, 3 lines); leave `run_no` and the receipt | ±3 |

Not candidates: upstream's `RunManager` beside `RunmanagerServer` and the rest (blacs's
tests import it); `new_sequence_details`' `script_path` and the other upstream
parameters, public and passed positionally; `timeout_time` beside `timeout` (a deadline
and a duration); the client's unprefixed `n_shots`, `error_in_globals` and others
(upstream's, public); the camelCase slots named after `.ui` widgets (upstream's); the
`'not queued'` and `'not sent'` values (documented to agents); `shutdown` throughout,
as labscript-utils and blacs's workers say; `say_hello` and the `handle_*` handlers,
one name each. Within runmanager: the globals readers in `__init__.py` are a chain,
not copies; the two duplicate-group-name checks differ deliberately (the live model,
and the state before a remote change); `new_global` and `new_group` work on different
models; the three queue-record literals differ in 2–4 keys and `_normalise_item`
fills the rest; `prepare_default_shot`'s folder listing seeds the index after a
restart, which the shared utility cannot. Seen for step 4: `nested`
(`__main__.py:246`) has no caller, and `log_if_global` (`:174`) is always passed
an empty list.

### Candidates in other repos

Found by this search; each is its owner's, routed by the coordinator after Ian decides.

| | Owner | Job | Copies | Proposal | Size |
| --- | --- | --- | --- | --- | --- |
| X1 | labscript-utils, then runmanager | Saving a TOML file. A bug that loses data: `labscript_profile.toml_config.dump_toml_file` (`:45`) writes in place, so an interrupted `save_appconfig` leaves a truncated app config | runmanager's `globals_file.save_document` (`globals_file.py:192`) writes a temp file, fsyncs, keeps the permissions and replaces | `dump_toml_file` does what `save_document` does; `save_document` keeps its HDF5 refusal and normalising and calls it | labscript-utils +12 −2; runmanager −20 +1 |
| X2 | labscript-utils, then each app | A server's port | runmanager (`__main__.py:4960`), blacs (`:312`), lyse (`:116`) and runviewer (`:1326`) read `ports/<app>` by hand; `ZMQClient` binds by `server` and `default_port`. blacs (`client.py:27`) and runviewer (`client.py:22`) spell the default port as a bare literal, runmanager as `DEFAULT_PORT` | `ZMQServer` takes `server` and `default_port` as `ZMQClient` does; each app's server sets them | labscript-utils ~+8; each app −1 +2 |
| X3 | blacs | Shot outcome statuses, and runmanager's address | `shot_execution.py` writes `'completed'`, `'aborted'`, `'failed'` and `'rejected'` as literals ten times (`:579`, `711`, `714`, `719`, `722`, `794`, `800`, `818`, `923`, `928`), beside runmanager's `BLACS_*` constants it already imports from; it rebuilds runmanager's host and port from the labconfig (`:113-116`) for its link light, beside `RunmanagerClient()` (`:199`) | Import the constants; the light takes the client's `host` and `port` | blacs ±10, −2 |
| X4 | runmanager, then labscript-agents and labscript-optimization | "lyse will have this shot" | `record['lyse'] in (LYSE_WAITING, LYSE_SENT)` in labscript-agents (`runmanager_interface.py:216`) and labscript-optimization (`session.py:175`) | One name beside the constants in `runmanager.client` | runmanager +2; ±1 each |
| X5 | labscript-agents, labscript-optimization | A logo on a label | `analysis_submission.set_icon_label_pixmap` (`:36`); the two windows (`window.py:30`, `window.py:96`) load `runmanager.svg` by hand | Leave: low value | — |

### Bugs found

- **B1 fix**, with D10. `is_valid_python_identifier` (`__init__.py:47`) accepts names
  Python rejects, such as `x²` and `٣`: a global so named is created, and no
  expression can name it. Upstream's.
- **B2 fix**. `RunmanagerServer._check_before_writing` (`__main__.py:5035`) accepts
  `bytes` with `raw=True`. TOML cannot hold bytes, and `_with_trailing_comment` raises
  on them, so the batch fails after earlier globals are written, against its "all or
  none". Accept `str` only.
- **B3 fix**. `handle_get_shot_output_folder` (`__main__.py:5374`) answers runmanager's
  working directory when the field is empty; `handle_get_labscript_file` beside it
  answers `''`.
- **B4 fix**, with D6. `send_to_runviewer` (`__main__.py:4636-4645`) reports a
  runviewer that did not start, then tries `add_shot` and reports again: two red lines
  for one fault.
- **B5 document**. A default shot's file drops a trailing `_<digits>` from the script's
  name (`scan_2.py` gives `…_scan_5.h5`, not `…_scan_2_5.h5`), because
  `next_available_indexed_filepath` strips a matching suffix even when given `start`
  (`__main__.py:4725`). Only the name is affected; D5 would fix it.
- **B6 fix**. `compile_labscript_with_globals_files_async`'s docstring
  (`__init__.py:919`) calls `output_path` a folder; it is the shot file. Upstream's.

### Decided and applied

Ian agreed to every candidate but X5 and D14, left. N1 adds 15 functions, not 16:
`queue_exchange` stays out of `remote`, as cut in step 2.1 (E). N4 renames
`globals_file.py` only, and the client's `path` stays. D5 fixes B5, which was
first listed in `KNOWN_ISSUES.md`.

Range for review: `0b324ce..3f569c1` on `CodeHealth`, one commit each: B2
ebb38d0, D1 f0352b2, D3 2495963, D2 f23fc54, D4 6fbcbac, D6 9d5e084, B4 711821e,
D7 cc52e36, D8 6f6cc74, D9 8ae9722, D10 and B1 6ee6cc8, D11 578bc39, D12
0b3d6d0, D13 999962b, N1 9d8d6bc, N2 8cf7889, N6 02f9341, N8 fefeb4b, B3
0fa004f, B6 15ae310, B5 40ca91f, N3 c337ba7, N4 eeaadd3, N5 090fba0, N7
5291d16, X4 eb28e07, X1 520e32f, X2 cf7cfb8, D5 and B5 3f569c1, over
labscript-utils 04a1dce's `strip_suffix`. 1e8cf64 gives the tests' lyse server a
free port, since lyse's server now binds lyse's own when given none (lyse
82e1bdc), and 73db099 builds the tests' runviewer server as runviewer 82ac827
now takes it.

D2 changes two things a user can see: a change of units now marks the globals
changed, as every other field does, so a preparse follows it; and the units
cell's Bool state is refreshed after any field changes. The suite is 150 tests,
with 30 subtests. B1, B2, B3 and B5 each have a test that fails without its fix.

Other repos change with this: labscript-optimization passes `sequence_id` to
`submit_shots` (N5) and calls `bound_for_lyse` (X4), as does labscript-agents;
blacs's integration test calls `get_save_data` (N7) and builds
`RunmanagerServer` on a port of its own (X2).

### Lab checks

- On Development, on Windows: runviewer started by a shot sent to it with View
  shot(s) ticked, now through `start_daemon` with `CREATE_NEW_PROCESS_GROUP`
  and `CREATE_NO_WINDOW` (D6). It should open its window with no console.
