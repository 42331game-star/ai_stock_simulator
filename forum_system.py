import os
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class Post:
    post_id: int
    agent_id: str
    content: str
    sentiment: float
    is_hot: bool = False
    parent_id: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.now)


class VirtualForum:
    def __init__(self, db, log_path: Optional[str] = None):
        self.db = db
        self.posts: List[Post] = []
        self._post_counter = 0
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.log_path = log_path or os.path.join(self.base_dir, "forum_log.md")
        self._load_posts_from_db()
        self._backfill_from_log_if_needed()

    def _load_posts_from_db(self):
        self.posts = []
        self._post_counter = 0
        for row in self.db.get_forum_posts():
            timestamp_value = row.get("timestamp")
            if isinstance(timestamp_value, str) and timestamp_value:
                try:
                    timestamp = datetime.fromisoformat(timestamp_value)
                except ValueError:
                    try:
                        timestamp = datetime.strptime(timestamp_value, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        timestamp = datetime.now()
            else:
                timestamp = datetime.now()

            post = Post(
                post_id=int(row["post_id"]),
                agent_id=row["agent_id"],
                content=row["content"],
                sentiment=float(row["sentiment"]),
                is_hot=bool(row["is_hot"]),
                parent_id=row["parent_id"],
                timestamp=timestamp,
            )
            self.posts.append(post)
            self._post_counter = max(self._post_counter, post.post_id)

    def _append_post_log(self, post: Post):
        timestamp = post.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        hot_tag = "🔥 [HOT]" if post.is_hot else ""
        reply_tag = f"↳ (Reply to #{post.parent_id})" if post.parent_id else "[Original]"
        sentiment_emoji = "🟢" if post.sentiment > 0.3 else "🔴" if post.sentiment < -0.3 else "⚪"

        # One write call per post keeps concurrent appends from interleaving
        # half-written lines between the two processes.
        entry = (
            f"**[{timestamp}] {hot_tag} {post.agent_id}** {sentiment_emoji} {reply_tag}\n"
            f"> {post.content}\n\n"
        )
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(entry)

    def _parse_log_posts(self) -> List[Post]:
        if not os.path.exists(self.log_path):
            return []

        posts: List[Post] = []
        pending_meta = None
        with open(self.log_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue

                if line.startswith("**[") and "]" in line and "**" in line:
                    try:
                        timestamp_text = line.split("]")[0][3:]
                        timestamp = datetime.strptime(timestamp_text, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        timestamp = datetime.now()

                    tail = line.split("] ", 1)[1] if "] " in line else line
                    tail = tail.replace("**", "").strip()
                    is_hot = "[HOT]" in tail
                    parent_id = None
                    if "Reply to #" in tail:
                        try:
                            parent_id = int(tail.split("Reply to #", 1)[1].split(")")[0])
                        except (ValueError, IndexError):
                            parent_id = None

                    agent_id = tail.split()[0] if tail.split() else "Unknown"
                    pending_meta = {
                        "timestamp": timestamp,
                        "agent_id": agent_id,
                        "is_hot": is_hot,
                        "parent_id": parent_id,
                    }
                    continue

                if pending_meta and line.startswith("> "):
                    content = line[2:].strip()
                    sentiment = 0.0
                    post = Post(
                        post_id=len(posts) + 1,
                        agent_id=pending_meta["agent_id"],
                        content=content,
                        sentiment=sentiment,
                        is_hot=pending_meta["is_hot"],
                        parent_id=pending_meta["parent_id"],
                        timestamp=pending_meta["timestamp"],
                    )
                    posts.append(post)
                    pending_meta = None
        return posts

    def _backfill_from_log_if_needed(self):
        parsed_posts = self._parse_log_posts()
        if not parsed_posts:
            return

        existing_keys = {
            (post.timestamp.strftime("%Y-%m-%d %H:%M:%S"), post.agent_id, post.content)
            for post in self.posts
        }

        inserted = 0
        for parsed in parsed_posts:
            key = (parsed.timestamp.strftime("%Y-%m-%d %H:%M:%S"), parsed.agent_id, parsed.content)
            if key in existing_keys:
                continue

            # Let the database allocate the id: a local counter could collide
            # with a post written by the other process in the meantime.
            parsed.post_id = self.db.save_forum_post(
                post_id=None,
                agent_id=parsed.agent_id,
                content=parsed.content,
                sentiment=parsed.sentiment,
                is_hot=parsed.is_hot,
                parent_id=parsed.parent_id,
                timestamp=parsed.timestamp.isoformat(sep=" ", timespec="seconds"),
            )
            self._post_counter = max(self._post_counter, parsed.post_id)
            self.posts.append(parsed)
            existing_keys.add(key)
            inserted += 1

        if inserted:
            self.posts.sort(key=lambda post: post.post_id)

    def add_post(
        self,
        agent_id: str,
        content: str,
        sentiment: float,
        is_hot: bool = False,
        parent_id: Optional[int] = None,
    ) -> Post:
        self._load_posts_from_db()
        new_post = Post(
            post_id=0,
            agent_id=agent_id,
            content=content,
            sentiment=sentiment,
            is_hot=is_hot,
            parent_id=parent_id,
        )
        # The database assigns the id atomically so two processes posting at the
        # same time cannot end up with the same post_id.
        new_post.post_id = self.db.save_forum_post(
            post_id=None,
            agent_id=new_post.agent_id,
            content=new_post.content,
            sentiment=new_post.sentiment,
            is_hot=new_post.is_hot,
            parent_id=new_post.parent_id,
            timestamp=new_post.timestamp.isoformat(sep=" ", timespec="seconds"),
        )
        self._post_counter = max(self._post_counter, new_post.post_id)
        self.posts.append(new_post)
        self._append_post_log(new_post)
        return new_post

    def get_feed(self, agent_id: str) -> List[Post]:
        self._load_posts_from_db()
        if not self.posts:
            return []

        latest_window = self.posts[-12:]
        latest_normal = [p for p in latest_window if not p.is_hot]
        latest_hot = [p for p in latest_window if p.is_hot]

        older_posts = self.posts[:-12]
        older_normal = [p for p in older_posts if not p.is_hot]
        older_hot = [p for p in older_posts if p.is_hot]

        feed: List[Post] = []

        if latest_normal:
            feed.extend(latest_normal[-4:])

        if latest_hot:
            feed.append(latest_hot[-1])
        elif older_hot:
            feed.append(random.choice(older_hot[-5:]))

        if older_normal:
            old_sample_size = min(len(older_normal), 2)
            feed.extend(random.sample(older_normal, old_sample_size))

        deduped: List[Post] = []
        seen_ids = set()
        for post in sorted(feed, key=lambda p: p.post_id):
            if post.post_id in seen_ids:
                continue
            seen_ids.add(post.post_id)
            deduped.append(post)

        return deduped[-6:]


if __name__ == "__main__":
    import tempfile

    from database import SimulationDB

    # The demo writes to a throwaway directory so it cannot corrupt real state.
    demo_dir = tempfile.mkdtemp(prefix="forum_demo_")
    forum = VirtualForum(
        SimulationDB(os.path.join(demo_dir, "forum_demo.db")),
        log_path=os.path.join(demo_dir, "forum_demo.md"),
    )
    for i in range(5):
        forum.add_post(f"Agent_{i}", f"I think stock {i} is going to the moon!", random.uniform(-1, 1))

    forum.add_post("Journalist_01", "EXCLUSIVE: Company X is facing a massive lawsuit!", 0.0, is_hot=True)
    feed = forum.get_feed("Agent_01")
    print(f"Feed size: {len(feed)}")
    for idx, post in enumerate(feed, start=1):
        hot_tag = "[HOT] " if post.is_hot else ""
        print(f"{idx}. {hot_tag}Agent {post.agent_id}: {post.content}")
