export default function Pending({ email, onRefresh, onSignOut }) {
  return (
    <div className="progress">
      <h2>Waiting for approval</h2>
      <p className="stage">{email}</p>
      <p className="reason">
        Your account exists but has not been approved yet. Once an administrator
        approves it, you will be able to add recordings.
      </p>
      <div className="pending-actions">
        <button className="btn primary" onClick={onRefresh}>
          Check again
        </button>
        <button className="btn" onClick={onSignOut}>
          Sign out
        </button>
      </div>
    </div>
  );
}
