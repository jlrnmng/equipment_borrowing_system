from app.utils.db import get_db
from app.models.member_request import MemberBorrowRequest
from app.models.member_return_request import MemberReturnRequest


def expire_stale_requests(expiry_minutes=30, auto_commit=True):
    """Auto-expire stale pending borrow/return requests.

    Expired requests remain available for reporting/audit via their status and
    review notes.
    """
    expiry_minutes = max(1, int(expiry_minutes or 30))
    db = get_db()
    MemberBorrowRequest._ensure_tables(db)
    MemberReturnRequest._ensure_tables(db)

    borrow_expired = 0
    return_expired = 0

    with db.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE member_borrow_requests
            SET status = 'expired',
                reviewed_at = NOW(),
                review_notes = COALESCE(
                    CONCAT(review_notes, '\\n[System] Auto-expired after {expiry_minutes} minutes pending review.'),
                    '[System] Auto-expired after {expiry_minutes} minutes pending review.'
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'pending'
              AND created_at < (NOW() - INTERVAL {expiry_minutes} MINUTE)
            """
        )
        borrow_expired = cursor.rowcount or 0

        cursor.execute(
            f"""
            UPDATE member_return_requests rr
            LEFT JOIN borrow_items bi ON bi.borrow_item_id = rr.borrow_item_id
            SET rr.status = 'expired',
                rr.reviewed_at = NOW(),
                rr.review_notes = COALESCE(
                    CONCAT(rr.review_notes, '\\n[System] Auto-expired after {expiry_minutes} minutes pending review.'),
                    '[System] Auto-expired after {expiry_minutes} minutes pending review.'
                ),
                rr.updated_at = CURRENT_TIMESTAMP
            WHERE rr.status = 'pending'
              AND rr.created_at < (NOW() - INTERVAL {expiry_minutes} MINUTE)
              AND (bi.returned_at IS NULL OR bi.borrow_item_id IS NULL)
            """
        )
        return_expired = cursor.rowcount or 0

    if auto_commit:
        db.commit()
    return {
        'borrow_requests_expired': borrow_expired,
        'return_requests_expired': return_expired,
        'expiry_minutes': expiry_minutes,
    }
