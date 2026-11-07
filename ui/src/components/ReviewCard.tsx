import type { ReviewResult } from "@houdini-pi/protocol";

const VERDICT: Record<ReviewResult["verdict"], string> = {
  aligned: "looks aligned",
  check: "worth a quick check",
  misaligned: "likely misaligned",
};

/** A second opinion on whether the artist and the agent understood each other. */
export function ReviewCard({ review, onAsk }: { review: ReviewResult; onAsk: (question: string) => void }) {
  return (
    <section className={`review verdict-${review.verdict}`} data-testid="review" data-verdict={review.verdict}>
      <header>
        <b>Second opinion</b>
        <span className={`chip-verdict ${review.verdict}`} data-testid="review-verdict">{VERDICT[review.verdict]}</span>
        <span className="muted" data-testid="review-reviewer" title={review.reviewer}>
          {review.differentFamily ? `by a ${review.reviewerFamily} model (the agent is ${review.agentFamily})` : `same model family as the agent (${review.agentFamily}): a weaker check`}
        </span>
      </header>
      <p className="review-summary">{review.summary}</p>
      {review.findings.map((finding, index) => (
        <article key={index} className={`finding severity-${finding.severity}`} data-testid="finding">
          <div className="finding-head">
            <span className={`badge ${finding.severity}`}>{finding.severity}</span>
            <span className="badge kind">{finding.kind}</span>
            <span className="muted">your message {finding.turn}</span>
          </div>
          <blockquote>{finding.userSaid}</blockquote>
          <p><b>The agent:</b> {finding.agentDid}</p>
          <p><b>Why it matters:</b> {finding.risk}</p>
          <div className="finding-ask">
            <span>{finding.askInstead}</span>
            <button type="button" data-testid="use-question" onClick={() => onAsk(finding.askInstead)} title="Put this in the message box to answer it">
              answer this
            </button>
          </div>
        </article>
      ))}
    </section>
  );
}
