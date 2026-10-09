// A like belongs to what a forwarded link shows: a book or chapter reading link, or a story video card.
export type MemoirShareLikeTarget =
  | { reading_token: string; video_code?: undefined }
  | { video_code: string; reading_token?: undefined };

export interface MemoirShareLiker {
  // Empty while the liker keeps WeChat's placeholder name.
  display_name: string;
  avatar_url: string | null;
}

export interface MemoirShareLikeSummary {
  count: number;
  likers: MemoirShareLiker[];
}

export interface MemoirShareLikes extends MemoirShareLikeSummary {
  liked: boolean;
  can_like: boolean;
  // Absent from servers deployed before the reminder existed.
  profile_missing?: boolean;
}

export interface MemoirShareLikeActivityItem {
  id: string;
  target_kind: "book" | "chapter" | "video";
  target_title: string;
  liker: MemoirShareLiker;
  created_at: string;
}

export interface MemoirShareLikeActivity {
  items: MemoirShareLikeActivityItem[];
}
