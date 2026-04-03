"""PyGithub wrapper for pr_clean.

This module provides the :class:`GitHubClient` class, a thin but ergonomic
wrapper around `PyGithub <https://pygithub.readthedocs.io/>`_ that exposes
exactly the operations pr_clean needs:

* Fetch a pull request's body text.
* Fetch all issue comments on a pull request.
* Update the PR body.
* Update (edit) a single issue comment.
* Post a new issue comment (e.g. a scan-results summary).

The client accepts either a plain GitHub personal-access token or a pre-built
``github.Github`` instance, making it straightforward to inject a mock in
tests without hitting the network.

PR references are accepted as:

* A full GitHub URL:  ``https://github.com/owner/repo/pull/42``
* A ``"owner/repo#42"`` shorthand string.
* Explicit ``repo`` (``"owner/repo"``) and ``pr_number`` (``int``) arguments.

Typical usage::

    from pr_clean.github_client import GitHubClient

    client = GitHubClient(token="ghp_...")
    pr_data = client.get_pr(repo="owner/repo", pr_number=42)
    print(pr_data.body)

    comments = client.get_pr_comments(repo="owner/repo", pr_number=42)
    for c in comments:
        print(c.id, c.body)

    # Strip and push back the PR body.
    client.update_pr_body(repo="owner/repo", pr_number=42, new_body="Clean text.")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union

try:
    from github import Github, GithubException, Auth
    from github.PullRequest import PullRequest
    from github.IssueComment import IssueComment
    from github.Repository import Repository
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "PyGithub is required for pr_clean.github_client. "
        "Install it with: pip install PyGithub"
    ) from exc


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------


@dataclass
class PRData:
    """Lightweight snapshot of a GitHub pull request.

    Attributes:
        repo: Repository identifier in ``owner/repo`` format.
        number: Pull request number.
        title: PR title string.
        body: PR description markdown (may be empty string, never ``None``).
        author: GitHub login of the PR author.
        html_url: Full URL to the PR on GitHub.
        state: PR state string: ``"open"`` or ``"closed"``.
    """

    repo: str
    number: int
    title: str
    body: str
    author: str
    html_url: str
    state: str


@dataclass
class CommentData:
    """Lightweight snapshot of a GitHub issue comment.

    Attributes:
        id: Numeric comment ID.
        body: Comment body markdown (may be empty string, never ``None``).
        author: GitHub login of the comment author.
        html_url: Full URL to the comment on GitHub.
        created_at: ISO 8601 string of the creation timestamp.
        updated_at: ISO 8601 string of the last-update timestamp.
    """

    id: int
    body: str
    author: str
    html_url: str
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# URL / reference parsing helpers
# ---------------------------------------------------------------------------

_PR_URL_RE: re.Pattern[str] = re.compile(
    r"https?://github\.com/([^/]+/[^/]+)/pull/(\d+)",
    re.IGNORECASE,
)
_PR_SHORTHAND_RE: re.Pattern[str] = re.compile(
    r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)$"
)


def parse_pr_reference(reference: str) -> Tuple[str, int]:
    """Parse a PR URL or shorthand string into ``(repo, pr_number)``.

    Accepted formats:

    * ``https://github.com/owner/repo/pull/42``
    * ``owner/repo#42``

    Args:
        reference: PR URL or ``owner/repo#number`` string.

    Returns:
        A ``(repo, pr_number)`` tuple where *repo* is ``"owner/repo"`` and
        *pr_number* is the integer PR number.

    Raises:
        ValueError: If *reference* cannot be parsed as either format.
    """
    # Try full URL first.
    m = _PR_URL_RE.search(reference)
    if m:
        return m.group(1), int(m.group(2))

    # Try shorthand.
    m = _PR_SHORTHAND_RE.match(reference.strip())
    if m:
        return m.group(1), int(m.group(2))

    raise ValueError(
        f"Cannot parse PR reference {reference!r}. "
        "Expected a GitHub PR URL (https://github.com/owner/repo/pull/N) "
        "or shorthand (owner/repo#N)."
    )


# ---------------------------------------------------------------------------
# GitHubClient
# ---------------------------------------------------------------------------


class GitHubClient:
    """Thin wrapper around PyGithub for pr_clean operations.

    The client lazily authenticates on first use.  All network errors from
    PyGithub are allowed to propagate as :class:`github.GithubException` so
    that callers can decide how to handle rate limits, permission errors, etc.

    Args:
        token: A GitHub personal access token (classic or fine-grained).
            Mutually exclusive with *github_instance*.
        github_instance: A pre-constructed :class:`github.Github` instance.
            Useful for testing with a mock or for passing a custom base URL
            (e.g. GitHub Enterprise).  Mutually exclusive with *token*.
        base_url: GitHub API base URL.  Defaults to the public GitHub API.
            Only used when *token* is supplied (ignored when
            *github_instance* is supplied directly).

    Raises:
        ValueError: If neither *token* nor *github_instance* is provided.

    Example::

        client = GitHubClient(token="ghp_...")
        pr = client.get_pr("owner/repo", 42)
        print(pr.title)
    """

    _DEFAULT_BASE_URL = "https://api.github.com"

    def __init__(
        self,
        token: Optional[str] = None,
        github_instance: Optional[Github] = None,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        """Initialise the client.

        Args:
            token: GitHub personal access token.
            github_instance: Pre-built :class:`github.Github` object.
            base_url: GitHub API base URL (only used with *token*).

        Raises:
            ValueError: If neither *token* nor *github_instance* is given.
        """
        if github_instance is not None:
            self._gh = github_instance
        elif token is not None:
            auth = Auth.Token(token)
            if base_url and base_url != self._DEFAULT_BASE_URL:
                self._gh = Github(base_url=base_url, auth=auth)
            else:
                self._gh = Github(auth=auth)
        else:
            raise ValueError(
                "GitHubClient requires either a 'token' or a 'github_instance'."
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_repo(self, repo: str) -> Repository:
        """Return the PyGithub Repository object for *repo*.

        Args:
            repo: ``"owner/repo"`` identifier.

        Returns:
            :class:`github.Repository.Repository` instance.

        Raises:
            github.GithubException: On API errors (404, 403, etc.).
        """
        return self._gh.get_repo(repo)

    @staticmethod
    def _pr_to_data(pr: PullRequest, repo: str) -> PRData:
        """Convert a PyGithub PullRequest to a :class:`PRData`.

        Args:
            pr: PyGithub pull request object.
            repo: Repository identifier string.

        Returns:
            Populated :class:`PRData` instance.
        """
        return PRData(
            repo=repo,
            number=pr.number,
            title=pr.title or "",
            body=pr.body or "",
            author=pr.user.login if pr.user else "",
            html_url=pr.html_url or "",
            state=pr.state or "",
        )

    @staticmethod
    def _comment_to_data(comment: IssueComment) -> CommentData:
        """Convert a PyGithub IssueComment to a :class:`CommentData`.

        Args:
            comment: PyGithub issue comment object.

        Returns:
            Populated :class:`CommentData` instance.
        """
        created = (
            comment.created_at.isoformat() if comment.created_at else ""
        )
        updated = (
            comment.updated_at.isoformat() if comment.updated_at else ""
        )
        return CommentData(
            id=comment.id,
            body=comment.body or "",
            author=comment.user.login if comment.user else "",
            html_url=comment.html_url or "",
            created_at=created,
            updated_at=updated,
        )

    # ------------------------------------------------------------------
    # Public API: read operations
    # ------------------------------------------------------------------

    def get_pr(
        self,
        repo: str,
        pr_number: int,
    ) -> PRData:
        """Fetch a pull request and return it as a :class:`PRData`.

        Args:
            repo: Repository identifier in ``"owner/repo"`` format.
            pr_number: The integer pull request number.

        Returns:
            Populated :class:`PRData` snapshot.

        Raises:
            github.GithubException: On API errors.
            ValueError: If *repo* or *pr_number* look obviously wrong.
        """
        if not repo or "/" not in repo:
            raise ValueError(
                f"Invalid repo identifier {repo!r}. Expected 'owner/repo' format."
            )
        if pr_number < 1:
            raise ValueError(f"pr_number must be a positive integer, got {pr_number!r}.")

        gh_repo = self._get_repo(repo)
        pr = gh_repo.get_pull(pr_number)
        return self._pr_to_data(pr, repo)

    def get_pr_from_url(self, url: str) -> PRData:
        """Fetch a pull request identified by its GitHub URL.

        Args:
            url: Full GitHub PR URL, e.g.
                ``"https://github.com/owner/repo/pull/42"``.

        Returns:
            Populated :class:`PRData` snapshot.

        Raises:
            ValueError: If *url* cannot be parsed as a PR URL.
            github.GithubException: On API errors.
        """
        repo, pr_number = parse_pr_reference(url)
        return self.get_pr(repo, pr_number)

    def get_pr_comments(
        self,
        repo: str,
        pr_number: int,
    ) -> List[CommentData]:
        """Fetch all issue comments on a pull request.

        Note: this fetches *issue* comments (the discussion thread), not
        *review* comments (inline code comments).  For most pr_clean use
        cases the discussion thread is what matters.

        Args:
            repo: Repository identifier in ``"owner/repo"`` format.
            pr_number: The integer pull request number.

        Returns:
            List of :class:`CommentData` objects in chronological order.
            Returns an empty list when there are no comments.

        Raises:
            github.GithubException: On API errors.
        """
        if not repo or "/" not in repo:
            raise ValueError(
                f"Invalid repo identifier {repo!r}. Expected 'owner/repo' format."
            )
        if pr_number < 1:
            raise ValueError(f"pr_number must be a positive integer, got {pr_number!r}.")

        gh_repo = self._get_repo(repo)
        # get_pull().get_issue_comments() returns all thread comments.
        pr = gh_repo.get_pull(pr_number)
        # Access the underlying issue to get issue comments.
        issue = gh_repo.get_issue(pr_number)
        comments = list(issue.get_comments())
        return [self._comment_to_data(c) for c in comments]

    def get_pr_and_comments(
        self,
        repo: str,
        pr_number: int,
    ) -> Tuple[PRData, List[CommentData]]:
        """Fetch both the PR body and all comments in one call.

        Args:
            repo: Repository identifier in ``"owner/repo"`` format.
            pr_number: The integer pull request number.

        Returns:
            A ``(PRData, [CommentData, ...])`` tuple.

        Raises:
            github.GithubException: On API errors.
        """
        pr_data = self.get_pr(repo, pr_number)
        comments = self.get_pr_comments(repo, pr_number)
        return pr_data, comments

    # ------------------------------------------------------------------
    # Public API: write operations
    # ------------------------------------------------------------------

    def update_pr_body(
        self,
        repo: str,
        pr_number: int,
        new_body: str,
    ) -> PRData:
        """Replace the body of a pull request.

        Args:
            repo: Repository identifier in ``"owner/repo"`` format.
            pr_number: The integer pull request number.
            new_body: The replacement markdown string for the PR description.

        Returns:
            Updated :class:`PRData` reflecting the new body.

        Raises:
            github.GithubException: On API errors (e.g. 403 Forbidden if the
                token lacks ``repo`` write scope).
        """
        if not repo or "/" not in repo:
            raise ValueError(
                f"Invalid repo identifier {repo!r}. Expected 'owner/repo' format."
            )
        if pr_number < 1:
            raise ValueError(f"pr_number must be a positive integer, got {pr_number!r}.")

        gh_repo = self._get_repo(repo)
        pr = gh_repo.get_pull(pr_number)
        pr.edit(body=new_body)
        return self._pr_to_data(pr, repo)

    def update_comment(
        self,
        repo: str,
        comment_id: int,
        new_body: str,
    ) -> CommentData:
        """Replace the body of an existing issue comment.

        Args:
            repo: Repository identifier in ``"owner/repo"`` format.
            comment_id: The numeric ID of the issue comment to update.
            new_body: Replacement markdown string for the comment body.

        Returns:
            Updated :class:`CommentData` reflecting the new body.

        Raises:
            github.GithubException: On API errors.
        """
        if not repo or "/" not in repo:
            raise ValueError(
                f"Invalid repo identifier {repo!r}. Expected 'owner/repo' format."
            )
        if comment_id < 1:
            raise ValueError(
                f"comment_id must be a positive integer, got {comment_id!r}."
            )

        gh_repo = self._get_repo(repo)
        comment = gh_repo.get_issue_comment(comment_id)
        comment.edit(body=new_body)
        return self._comment_to_data(comment)

    def post_comment(
        self,
        repo: str,
        pr_number: int,
        body: str,
    ) -> CommentData:
        """Post a new issue comment on a pull request.

        Args:
            repo: Repository identifier in ``"owner/repo"`` format.
            pr_number: The integer pull request number.
            body: The markdown body of the new comment.

        Returns:
            :class:`CommentData` for the newly created comment.

        Raises:
            github.GithubException: On API errors.
        """
        if not repo or "/" not in repo:
            raise ValueError(
                f"Invalid repo identifier {repo!r}. Expected 'owner/repo' format."
            )
        if pr_number < 1:
            raise ValueError(f"pr_number must be a positive integer, got {pr_number!r}.")
        if not body:
            raise ValueError("Comment body must not be empty.")

        gh_repo = self._get_repo(repo)
        issue = gh_repo.get_issue(pr_number)
        comment = issue.create_comment(body=body)
        return self._comment_to_data(comment)

    def delete_comment(
        self,
        repo: str,
        comment_id: int,
    ) -> None:
        """Delete an existing issue comment.

        Args:
            repo: Repository identifier in ``"owner/repo"`` format.
            comment_id: The numeric ID of the issue comment to delete.

        Raises:
            github.GithubException: On API errors.
        """
        if not repo or "/" not in repo:
            raise ValueError(
                f"Invalid repo identifier {repo!r}. Expected 'owner/repo' format."
            )
        if comment_id < 1:
            raise ValueError(
                f"comment_id must be a positive integer, got {comment_id!r}."
            )

        gh_repo = self._get_repo(repo)
        comment = gh_repo.get_issue_comment(comment_id)
        comment.delete()

    # ------------------------------------------------------------------
    # High-level composite helpers
    # ------------------------------------------------------------------

    def strip_pr_body(
        self,
        repo: str,
        pr_number: int,
        clean_body: str,
        dry_run: bool = False,
    ) -> Tuple[bool, PRData]:
        """Update the PR body with *clean_body* unless *dry_run* is set.

        Args:
            repo: Repository identifier in ``"owner/repo"`` format.
            pr_number: The integer pull request number.
            clean_body: The cleaned markdown to push back to GitHub.
            dry_run: When ``True``, fetch the current PR but do **not** write
                anything back.  Returns ``(False, current_pr_data)``.

        Returns:
            A ``(was_updated, PRData)`` tuple.  *was_updated* is ``True`` when
            the body was actually changed on GitHub, ``False`` when *dry_run*
            is set or when *clean_body* equals the current body.

        Raises:
            github.GithubException: On API errors.
        """
        current = self.get_pr(repo, pr_number)
        if dry_run or clean_body == current.body:
            return False, current
        updated = self.update_pr_body(repo, pr_number, clean_body)
        return True, updated

    def strip_comment(
        self,
        repo: str,
        comment_id: int,
        clean_body: str,
        dry_run: bool = False,
    ) -> Tuple[bool, CommentData]:
        """Update an issue comment with *clean_body* unless *dry_run* is set.

        Args:
            repo: Repository identifier in ``"owner/repo"`` format.
            comment_id: The numeric comment ID.
            clean_body: The cleaned markdown to push back to GitHub.
            dry_run: When ``True``, fetch the comment but do **not** write.

        Returns:
            A ``(was_updated, CommentData)`` tuple.

        Raises:
            github.GithubException: On API errors.
        """
        gh_repo = self._get_repo(repo)
        comment = gh_repo.get_issue_comment(comment_id)
        current_data = self._comment_to_data(comment)
        if dry_run or clean_body == current_data.body:
            return False, current_data
        comment.edit(body=clean_body)
        updated_data = self._comment_to_data(comment)
        return True, updated_data
