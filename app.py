                       unsafe_allow_html=True)
            c.markdown('<div class="step"><b><i>3</i>Download</b>'
                       '<span>Save the file and post it straight to Instagram.</span></div>',
                       unsafe_allow_html=True)

    # ================================================================
    # SAVE VIDEO  (Twitter/X · Instagram · TikTok)
    # ================================================================
    with tab_save:
        st.markdown("Paste a link to a public video post and save the file. "
                    "Twitter/X works through FxTwitter; Instagram and TikTok use "
                    "[yt-dlp](https://github.com/yt-dlp/yt-dlp). No API keys needed.")
        dl_url = st.text_input(
            "Video link", key="dl_url",
            placeholder="instagram.com/reel/…  ·  tiktok.com/@user/video/…  ·  x.com/user/status/…")

        platform = detect_platform(dl_url) if dl_url.strip() else None
        if platform:
            cls = {"twitter": "badge-twitter", "instagram": "badge-insta", "tiktok": "badge-tiktok"}[platform]
            label = {"twitter": "Twitter / X", "instagram": "Instagram", "tiktok": "TikTok"}[platform]
            st.markdown(f'Detected <span class="badge {cls}">{label}</span>', unsafe_allow_html=True)
        elif dl_url.strip():
            st.warning("That link isn't from Twitter/X, Instagram or TikTok.")

        dl_n = 1
        if platform == "twitter":
            dl_n = int(st.number_input("Video # (if the tweet has several)", 1, 4, 1, key="dl_vn"))

        cookies = None
        with st.expander("Login needed? (Instagram)"):
            st.markdown(
                "Instagram often blocks servers and asks for a login. If a download fails with that "
                "message, export a **cookies.txt** from a browser where you're signed in "
                "(a “Get cookies.txt” browser extension does this) and upload it here. "
                "The file is used for this download only and isn't saved.")
            up = st.file_uploader("cookies.txt", type=["txt"], key="dl_cookies")
            if up is not None:
                cookies = up.getvalue()

        if stretch(st.button, "Download video", type="primary", key="dl_btn"):
            if not dl_url.strip():
                st.warning("Paste a video link first.")
            elif not platform:
                st.error("Use a link from Twitter/X, Instagram or TikTok.")
            else:
                t0 = time.time()
                try:
                    with st.spinner(f"Downloading from {platform.title()}…"):
                        r = generate_download(dl_url, dl_n, cookies)
                    r["secs"] = time.time() - t0
                    st.session_state["dl_result"] = r
                except DownloadError as e:
                    st.session_state.pop("dl_result", None)
                    st.error(e.friendly)
                    if e.raw:
                        with st.expander("Details"):
                            st.code(e.raw)
                except Exception as e:
                    st.session_state.pop("dl_result", None)
                    st.error(f"Download failed: {e}")

        dl_res = st.session_state.get("dl_result")
        if dl_res:
            with st.container(border=True):
                st.success(f"Saved in {dl_res['secs']:.1f} s · {len(dl_res['data']) / (1024 * 1024):.1f} MB")
                _, mid, _ = st.columns([1, 2, 1])
                mid.video(dl_res["data"])
                stretch(st.download_button, "Save to device", dl_res["data"],
                        file_name=dl_res["name"], mime=dl_res["mime"], key="dl_save")
        else:
            rule()
            cols = st.columns(3)
            cols[0].markdown('<div class="platform"><b>Twitter / X</b><span>Video tweets and GIFs</span></div>',
                             unsafe_allow_html=True)
            cols[1].markdown('<div class="platform"><b>Instagram</b><span>Reels and video posts</span></div>',
                             unsafe_allow_html=True)
            cols[2].markdown('<div class="platform"><b>TikTok</b><span>Public videos</span></div>',
                             unsafe_allow_html=True)

    # ================================================================
    # GUIDE
    # ================================================================
    with tab_guide:
        st.markdown(
            "### Tweet → Photo / Video\n"
            "1. On X, open a public tweet and copy its link (**Share → Copy link**).\n"
            "2. In **Create**, paste it and choose **Photo**, **Video** (a 1080 × 1920 reel) or **Just Video**.\n"
            "3. Adjust the options, press **Generate**, then **Download**.\n\n"
            "### Options\n"
            "- **Safe zone** keeps text 135 px from the sides and 250 px from the top and bottom, "
            "so Instagram never covers it. On by default.\n"
            "- **Profile info** has three settings: show everything, hide the `@username`, or hide the "
            "name and `@username` so only the tweet text is left.\n"
            "- **Font** lists 40+ typefaces. The first time you pick one that isn't installed, it is "
            "downloaded and cached. The sample under the menu shows how it looks.\n"
            "- **Video** adds a background (white, blur, black), tweet on top or bottom, crop to 1:1 and flip.\n"
            "- **Just Video** gives you the original file, nothing added.\n\n"
            "### Save video\n"
            "Paste a Twitter/X, Instagram or TikTok link and press **Download video**. "
            "Instagram sometimes asks for a login. Upload a `cookies.txt` under **Login needed?** if it does.\n\n"
            "### Good to know\n"
            "- Only **public** posts work.\n"
            "- Emojis are removed from rendered text, because most fonts can't draw them.\n"
            "- A photo of a video tweet shows its thumbnail with a play icon.\n"
            "- Instagram blocks many cloud servers. Running the app on your own computer is the most reliable way to save from it."
        )

    st.markdown('<p class="footer-text">Tweet to Reel · personal use · '
                'FxTwitter API & yt-dlp</p>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
