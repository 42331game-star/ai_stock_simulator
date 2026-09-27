import os
import random
import tempfile

from database import SimulationDB
from forum_system import VirtualForum


def test_social_features():
    # Isolated throwaway database and log: the test must not touch real state.
    demo_dir = tempfile.mkdtemp(prefix="forum_test_")
    forum = VirtualForum(
        SimulationDB(os.path.join(demo_dir, "test_forum.db")),
        log_path=os.path.join(demo_dir, "forum_log.md"),
    )
    print("Testing Social Forum Features...")
    print("-" * 50)

    for i in range(10):
        forum.add_post(
            f"Agent_{i:03d}",
            f"I think the market is {random.choice(['going up', 'crashing', 'stable'])}!",
            random.uniform(-1, 1),
        )

    forum.add_post("Journalist_01", "BREAKING: VDA CEO spotted selling all shares!", -0.8, is_hot=True)

    hot_post = forum.posts[-1]
    forum.add_post("Retail_01", "OMG!! I'm selling everything now!!", -1.0, parent_id=hot_post.post_id)
    forum.add_post("Boss_01", "This is a great opportunity to buy the dip.", 0.9, parent_id=hot_post.post_id)

    post_ids = [post.post_id for post in forum.posts]
    assert len(post_ids) == len(set(post_ids)), f"duplicate post ids: {post_ids}"
    assert len(forum.posts) == 13, f"expected 13 posts, got {len(forum.posts)}"

    print("\nTesting Agent Feed (Noise Reduction):")
    user_feed = forum.get_feed("Test_User")
    print(f"Feed size: {len(user_feed)}")

    for i, post in enumerate(user_feed):
        hot_tag = "[HOT] " if post.is_hot else ""
        reply_tag = f"(Reply to #{post.parent_id}) " if post.parent_id else ""
        print(f"{i+1}. {hot_tag}{reply_tag}Agent {post.agent_id}: {post.content}")

    print("-" * 50)
    print("Forum test complete.")


if __name__ == "__main__":
    test_social_features()
