import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  managementClient,
  recordId,
  recordTitle,
  type ManagedRecord,
  type ManagementClient,
  type ManagementAccess,
  type RecordKind,
} from "@/lib/memoryManagement";
import "./MemoryManagementPage.css";

const errorText = (error: unknown) =>
  error instanceof Error ? error.message : "Could not load records. Try again.";

export function MemoryManagementPage({ client = managementClient }: { client?: ManagementClient }) {
  const [access, setAccess] = useState<ManagementAccess | null>(null);
  const [kind, setKind] = useState<RecordKind>("memories");
  const [tier, setTier] = useState("");
  const [status, setStatus] = useState("active");
  const [project, setProject] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [records, setRecords] = useState<ManagedRecord[]>([]);
  const [total, setTotal] = useState<number>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<ManagedRecord | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [action, setAction] = useState<"archive" | "delete" | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [notice, setNotice] = useState("");
  const detailRequest = useRef(0);
  const savingLock = useRef(false);
  const actionButton = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    let active = true;
    setAccess(null);
    client
      .access()
      .then((result) => {
        if (active) setAccess(result);
      })
      .catch((err) => {
        if (active) setAccess({ available: false, reason: errorText(err) });
      });
    return () => {
      active = false;
    };
  }, [client, revision]);

  useEffect(() => {
    let active = true;
    detailRequest.current++;
    setDetail(null);
    setDetailError("");
    setDetailLoading(false);
    setRecords([]);
    setTotal(undefined);
    setError("");
    if (!access?.available) {
      setLoading(false);
      return;
    }
    setLoading(true);
    client
      .list({ kind, tier, status, project, offset })
      .then((result) => {
        if (active) {
          setRecords(result.records);
          setTotal(result.total);
        }
      })
      .catch((err) => {
        if (active) setError(errorText(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [client, access, kind, tier, status, project, offset]);

  async function inspect(record: ManagedRecord) {
    const requestId = ++detailRequest.current;
    setDetail(null);
    setDetailLoading(true);
    setDetailError("");
    setNotice("");
    try {
      const result = await client.detail(kind, recordId(record));
      if (requestId === detailRequest.current) setDetail(result);
    } catch (err) {
      if (requestId === detailRequest.current) setDetailError(errorText(err));
    } finally {
      if (requestId === detailRequest.current) setDetailLoading(false);
    }
  }

  async function confirmAction() {
    if (
      !detail ||
      !action ||
      savingLock.current ||
      (action === "delete" && confirmation !== recordId(detail))
    )
      return;
    savingLock.current = true;
    setSaving(true);
    setSaveError("");
    try {
      await client.remove(recordId(detail), action === "archive");
      setNotice(
        action === "archive" ? "Selected memory archived." : "Selected memory permanently deleted.",
      );
      setAction(null);
      setDetail(null);
      setRevision((value) => value + 1);
    } catch (err) {
      setSaveError(errorText(err));
    } finally {
      savingLock.current = false;
      setSaving(false);
    }
  }

  const visible = records.filter((record) =>
    `${recordTitle(record)} ${recordId(record)} ${record.project ?? ""}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const changeView = () => {
    setOffset(0);
    setQuery("");
    setNotice("");
  };
  return (
    <main className="memory-management text-foreground">
      <header className="memory-header">
        <div>
          <p className="memory-eyebrow text-muted-foreground">AUTOMATON MEMORY SYSTEM</p>
          <h1>Memory &amp; Handoffs</h1>
          <p className="text-muted-foreground">
            Inspect saved knowledge and pending work in one place.
          </p>
        </div>
        <Button variant="outline" onClick={() => setRevision((value) => value + 1)}>
          Refresh
        </Button>
      </header>
      <Tabs
        value={kind}
        onValueChange={(value) => {
          setKind(value as RecordKind);
          changeView();
        }}
      >
        <TabsList aria-label="Record type">
          <TabsTrigger value="memories">Memories</TabsTrigger>
          <TabsTrigger value="continuations">Continuations</TabsTrigger>
        </TabsList>
        <TabsContent value={kind}>
          <section
            aria-label={kind === "memories" ? "Memories" : "Continuations"}
            className="memory-body"
          >
            {!access && <p role="status">Checking access…</p>}
            {access && !access.available && (
              <div className="memory-panel" role="status">
                <h2>Live management unavailable</h2>
                <p>
                  {access.reason ||
                    "Your AOS account has not been verified against an AMS owner. No records have been requested."}
                </p>
              </div>
            )}
            {access?.available && (
              <>
                {kind === "continuations" && (
                  <p className="text-muted-foreground">
                    Pending, non-expired continuations only · up to 50. Completed and claimed
                    history is not included. Removal is unavailable until its lifecycle is approved
                    and supported.
                  </p>
                )}
                <div className="memory-filters">
                  {kind === "memories" ? (
                    <>
                      <label>
                        Tier
                        <select
                          value={tier}
                          onChange={(event) => {
                            setTier(event.target.value);
                            changeView();
                          }}
                        >
                          <option value="">All tiers</option>
                          {["episodic", "semantic", "procedural"].map((value) => (
                            <option key={value}>{value}</option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Status
                        <select
                          value={status}
                          onChange={(event) => {
                            setStatus(event.target.value);
                            changeView();
                          }}
                        >
                          <option value="active">Active</option>
                          <option value="archived">Archived</option>
                        </select>
                      </label>
                    </>
                  ) : (
                    <label>
                      Project (exact match)
                      <input
                        value={project}
                        maxLength={200}
                        onChange={(event) => {
                          setProject(event.target.value);
                          changeView();
                        }}
                      />
                    </label>
                  )}
                  <label>
                    Filter loaded titles or IDs
                    <input
                      type="search"
                      value={query}
                      maxLength={500}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder="Within this page only"
                    />
                  </label>
                </div>
                <p className="text-muted-foreground">
                  {kind === "memories"
                    ? "Browse by tier and status. This title filter does not search full memory content."
                    : "Opening a continuation only inspects it; it does not claim or resume work."}
                </p>
                {notice && <p role="status">{notice}</p>}
                {error && <p role="alert">{error}</p>}
                <div className="memory-columns">
                  <section className="memory-panel" aria-label="Record list" aria-busy={loading}>
                    <h2>{kind === "memories" ? "Memories" : "Pending continuations"}</h2>
                    {loading ? (
                      <p role="status">Loading records…</p>
                    ) : (
                      !error && (
                        <>
                          <p className="text-muted-foreground">
                            {visible.length} shown
                            {total !== undefined ? ` · ${total} matching records` : ""}
                          </p>
                          {visible.length === 0 && (
                            <p>
                              {records.length
                                ? "No loaded titles match this filter."
                                : "No records match these filters."}
                            </p>
                          )}
                          <ul className="memory-records">
                            {visible.map((record) => (
                              <li key={recordId(record)}>
                                <button
                                  type="button"
                                  className="memory-record"
                                  aria-pressed={
                                    detail !== null && recordId(detail) === recordId(record)
                                  }
                                  onClick={() => void inspect(record)}
                                >
                                  <strong>{recordTitle(record)}</strong>
                                  <span className="text-muted-foreground">
                                    {record.memory_tier || record.project || "No project"} ·{" "}
                                    {record.status}
                                  </span>
                                  <small>{recordId(record)}</small>
                                </button>
                              </li>
                            ))}
                          </ul>
                        </>
                      )
                    )}
                    {kind === "memories" && (
                      <nav aria-label="Memory pages" className="memory-actions">
                        <Button
                          variant="outline"
                          disabled={loading || offset === 0}
                          onClick={() => setOffset(Math.max(0, offset - 25))}
                        >
                          Previous
                        </Button>
                        <span>Page {Math.floor(offset / 25) + 1}</span>
                        <Button
                          variant="outline"
                          disabled={loading || total === undefined || offset + 25 >= total}
                          onClick={() => setOffset(offset + 25)}
                        >
                          Next
                        </Button>
                      </nav>
                    )}
                  </section>
                  <section
                    className="memory-panel memory-detail"
                    aria-label="Record detail"
                    aria-busy={detailLoading}
                  >
                    <h2>Inspection</h2>
                    {detailLoading && <p role="status">Loading full record…</p>}
                    {detailError && <p role="alert">{detailError}</p>}
                    {!detail && !detailLoading && !detailError && (
                      <p className="text-muted-foreground">
                        Select a record to inspect its content and metadata.
                      </p>
                    )}
                    {detail && (
                      <>
                        <h3>{recordTitle(detail)}</h3>
                        <p className="memory-id">{recordId(detail)}</p>
                        {kind === "memories" ? (
                          <>
                            <h3>Full content</h3>
                            <pre>
                              {detail.full_content ?? detail.content ?? "Content unavailable"}
                            </pre>
                          </>
                        ) : (
                          <>
                            {[
                              "original_goal",
                              "next_action",
                              "handoff_notes",
                              "blockers",
                              "remaining_subtasks",
                              "completed_subtasks",
                            ].map((field) => (
                              <div key={field}>
                                <h3>{field.replaceAll("_", " ")}</h3>
                                <pre>
                                  {typeof detail[field] === "string"
                                    ? detail[field]
                                    : JSON.stringify(detail[field] ?? [], null, 2)}
                                </pre>
                              </div>
                            ))}
                          </>
                        )}
                        <details>
                          <summary>All metadata and timestamps</summary>
                          <pre>
                            {JSON.stringify(
                              Object.fromEntries(
                                Object.entries(detail).filter(
                                  ([key]) => !["content", "full_content"].includes(key),
                                ),
                              ),
                              null,
                              2,
                            )}
                          </pre>
                        </details>
                        {kind === "memories" && (
                          <div className="memory-actions">
                            <Button
                              variant="outline"
                              disabled={detail.status !== "active"}
                              onClick={(event) => {
                                actionButton.current = event.currentTarget;
                                setAction("archive");
                                setSaveError("");
                              }}
                            >
                              Archive memory
                            </Button>
                            <Button
                              variant="destructive"
                              onClick={(event) => {
                                actionButton.current = event.currentTarget;
                                setAction("delete");
                                setConfirmation("");
                                setSaveError("");
                              }}
                            >
                              Delete permanently
                            </Button>
                          </div>
                        )}
                      </>
                    )}
                  </section>
                </div>
              </>
            )}
          </section>
        </TabsContent>
      </Tabs>
      <Dialog
        open={action !== null}
        onOpenChange={(open) => {
          if (!open && !savingLock.current) setAction(null);
        }}
      >
        <DialogContent
          showCloseButton={false}
          className="memory-confirm"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            actionButton.current?.focus();
          }}
        >
          <DialogTitle>
            {action === "archive"
              ? "Archive selected memory?"
              : "Permanently delete selected memory?"}
          </DialogTitle>
          <DialogDescription>
            {action === "archive"
              ? "Moves this memory out of active results. It remains in the archived list."
              : "Deletes this memory and its stored file. This cannot be undone."}
          </DialogDescription>
          <p className="break-all">{detail && recordTitle(detail)}</p>
          <code className="break-all">{detail && recordId(detail)}</code>
          {action === "delete" && (
            <label>
              Type the exact memory ID to confirm
              <input
                aria-label="Confirm memory ID"
                autoComplete="off"
                value={confirmation}
                disabled={saving}
                onChange={(event) => setConfirmation(event.target.value)}
              />
            </label>
          )}
          {saveError && <p role="alert">{saveError}</p>}
          <div className="memory-actions">
            <Button variant="outline" disabled={saving} onClick={() => setAction(null)}>
              Cancel
            </Button>
            <Button
              variant={action === "delete" ? "destructive" : "default"}
              disabled={
                saving || (action === "delete" && confirmation !== (detail && recordId(detail)))
              }
              onClick={() => void confirmAction()}
            >
              {saving
                ? "Saving…"
                : action === "archive"
                  ? "Confirm archive"
                  : "Confirm permanent deletion"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </main>
  );
}
