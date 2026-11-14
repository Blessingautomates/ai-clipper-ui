-- Prepared only. Test on an isolated project first; production requires approval.
BEGIN;
-- Storage policies combine with OR. Unknown read policies can expose clips;
-- unknown write policies can allow browser uploads, replacement, or deletion.
-- Stop for review rather than preserve any unreviewed storage policy.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = 'storage.objects'::regclass
    AND polname <> 'clips_read_own') THEN
    RAISE EXCEPTION 'Existing Storage policies require review; no changes applied';
  END IF;
END;
$$;
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('clips', 'clips', false, 104857600, ARRAY['video/mp4'])
ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM storage.buckets WHERE id = 'clips' AND NOT public) THEN
    RAISE EXCEPTION 'clips bucket must be private; no existing bucket changed';
  END IF;
END;
$$;
DROP POLICY IF EXISTS clips_read_own ON storage.objects;
CREATE POLICY clips_read_own ON storage.objects FOR SELECT TO authenticated
USING (bucket_id = 'clips' AND (storage.foldername(name))[1] = (SELECT auth.uid())::text);
COMMIT;
