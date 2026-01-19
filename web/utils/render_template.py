from info import BIN_CHANNEL, URL
from utils import temp
from web.utils.custom_dl import TGCustomYield
import urllib.parse
import aiofiles, html

watch_tmplt = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta property="og:image" content="https://i.ibb.co/M8S0Zzj/live-streaming.png" itemprop="thumbnailUrl">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Premium Filmax Player</title>

  <!-- Fonts & Icons -->
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Poppins:wght@500;600;700;800&display=swap">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <link rel="stylesheet" href="https://cdn.plyr.io/3.7.8/plyr.css" />

  <style>
    :root {
      --primary: #6366f1;
      --primary-hover: #4f46e5;
      --text-primary: #f9fafb;
      --text-secondary: #d1d5db;
      --bg-color: #0b1120;
      --player-bg: rgba(30, 41, 59, 0.85);
      --footer-bg: rgba(15, 23, 42, 0.9);
      --border-color: #374151;
      --glass: rgba(255, 255, 255, 0.08);
      --glass-hover: rgba(255, 255, 255, 0.12);
    }

    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }

    body {
      font-family: 'Inter', sans-serif;
      background: linear-gradient(135deg, #0b1120 0%, #1e293b 50%, #0f172a 100%);
      color: var(--text-primary);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      position: relative;
      overflow-x: hidden;
    }

    body::before {
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      background: radial-gradient(circle at 10% 20%, rgba(99, 102, 241, 0.1) 0%, transparent 20%),
                  radial-gradient(circle at 80% 10%, rgba(79, 70, 229, 0.1) 0%, transparent 20%),
                  radial-gradient(circle at 40% 80%, rgba(139, 92, 246, 0.1) 0%, transparent 20%);
      z-index: -1;
    }

    header {
      padding: 1.2rem;
      background: var(--glass);
      backdrop-filter: blur(16px);
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      justify-content: center;
      align-items: center;
      border-bottom: 1px solid var(--border-color);
    }

    .logo {
      display: flex;
      align-items: center;
      gap: 0.8rem;
      font-family: 'Poppins', sans-serif;
      font-weight: 700;
      font-size: 1.4rem;
      color: var(--text-primary);
      position: absolute;
      left: 1.5rem;
    }

    .logo i {
      color: var(--primary);
      font-size: 1.6rem;
    }

    .container {
      flex: 1;
      display: flex;
      justify-content: center;
      align-items: center;
      padding: 2rem;
      width: 100%;
    }

    .player-wrapper {
      width: 100%;
      max-width: 1100px;
      position: relative;
    }

    .player-container {
      width: 100%;
      background: var(--player-bg);
      border-radius: 1.2rem;
      overflow: hidden;
      backdrop-filter: blur(20px);
      box-shadow: 0 15px 35px rgba(0, 0, 0, 0.5);
      transition: transform 0.3s ease;
      border: 1px solid var(--border-color);
    }

    .player-container:hover {
      transform: translateY(-5px);
    }

    .video-wrapper {
      position: relative;
      padding: 56.25% 0 0 0; /* 16:9 Aspect Ratio */
    }

    .video-wrapper video {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
    }

    .file-name-container {
      padding: 1.2rem 1.5rem;
      text-align: center;
      border-bottom: 1px solid var(--border-color);
      background: rgba(15, 23, 42, 0.6);
    }

    #file-name {
      font-size: 1.1rem;
      font-weight: 600;
      color: var(--text-primary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      text-shadow: 0 2px 4px rgba(0, 0, 0, 0.5);
    }

    .action-buttons {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      padding: 1.5rem;
    }

    .action-btn {
      background: var(--glass);
      color: var(--text-primary);
      border: 1px solid var(--border-color);
      padding: 1rem 1.5rem;
      border-radius: 0.8rem;
      font-weight: 500;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.8rem;
      text-decoration: none;
      font-size: 0.95rem;
      transition: all 0.3s ease;
      text-align: center;
      backdrop-filter: blur(10px);
      box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
      min-height: 54px;
    }

    .action-btn:active {
      transform: translateY(2px);
      box-shadow: 0 2px 15px rgba(99, 102, 241, 0.4);
    }

    .action-btn:hover {
      background: var(--glass-hover);
      border-color: rgba(99, 102, 241, 0.5);
      transform: translateY(-2px);
      box-shadow: 0 6px 12px rgba(0, 0, 0, 0.15), 0 0 15px rgba(99, 102, 241, 0.4);
    }

    .download-btn {
      background: linear-gradient(135deg, rgba(99, 102, 241, 0.2), rgba(79, 70, 229, 0.2));
    }

    .download-btn:hover {
      background: linear-gradient(135deg, rgba(99, 102, 241, 0.3), rgba(79, 70, 229, 0.3));
    }

    .copy-btn {
      background: linear-gradient(135deg, rgba(156, 163, 175, 0.2), rgba(107, 114, 128, 0.2));
    }

    .copy-btn:hover {
      background: linear-gradient(135deg, rgba(156, 163, 175, 0.3), rgba(107, 114, 128, 0.3));
    }

    .vlc-btn {
      background: linear-gradient(135deg, rgba(255, 136, 0, 0.2), rgba(255, 102, 0, 0.2));
    }

    .vlc-btn:hover {
      background: linear-gradient(135deg, rgba(255, 136, 0, 0.3), rgba(255, 102, 0, 0.3));
    }

    .mx-btn {
      background: linear-gradient(135deg, rgba(0, 200, 83, 0.2), rgba(0, 150, 36, 0.2));
    }

    .mx-btn:hover {
      background: linear-gradient(135deg, rgba(0, 200, 83, 0.3), rgba(0, 150, 36, 0.3));
    }

    .playit-btn {
      background: linear-gradient(135deg, rgba(41, 98, 255, 0.2), rgba(0, 57, 203, 0.2));
    }

    .playit-btn:hover {
      background: linear-gradient(135deg, rgba(41, 98, 255, 0.3), rgba(0, 57, 203, 0.3));
    }

    .player-icon {
      width: 24px;
      height: 24px;
      object-fit: contain;
      filter: brightness(0) invert(1);
    }

    .info-section {
      padding: 1.5rem;
      border-top: 1px solid var(--border-color);
      background: rgba(15, 23, 42, 0.6);
    }

    .disclaimer {
      background: rgba(0, 0, 0, 0.2);
      padding: 1rem;
      border-radius: 0.5rem;
      margin-bottom: 1.5rem;
      font-size: 0.9rem;
      line-height: 1.5;
      color: var(--text-secondary);
    }

    .copyright {
      text-align: center;
      font-size: 0.85rem;
      color: var(--text-secondary);
      padding-top: 1rem;
      border-top: 1px solid var(--border-color);
      margin-bottom: 1rem;
    }

    .social-links {
      display: flex;
      justify-content: center;
      gap: 1.5rem;
      margin-top: 1rem;
    }

    .social-link {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: var(--glass);
      color: var(--text-primary);
      text-decoration: none;
      transition: all 0.3s ease;
      border: 1px solid var(--border-color);
    }

    .social-link:hover {
      transform: translateY(-3px);
      background: var(--primary);
      box-shadow: 0 5px 15px rgba(99, 102, 241, 0.4);
    }

    .toast {
      position: fixed;
      bottom: 20px;
      left: 50%;
      transform: translateX(-50%);
      background: var(--primary);
      color: white;
      padding: 0.8rem 1.5rem;
      border-radius: 0.5rem;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
      z-index: 1000;
      opacity: 0;
      transition: opacity 0.3s ease;
    }

    .toast.show {
      opacity: 1;
    }

    footer {
      padding: 1.5rem;
      text-align: center;
      background: var(--footer-bg);
      color: var(--text-secondary);
      font-size: 0.9rem;
      border-top: 1px solid var(--border-color);
      margin-top: auto;
    }

    @media (max-width: 768px) {
      header {
        padding: 1rem;
      }
      
      .logo {
        position: relative;
        left: 0;
        margin-bottom: 0.5rem;
        justify-content: center;
      }
      
      #file-name {
        font-size: 0.9rem;
      }
      
      .container {
        padding: 1rem;
      }
      
      .action-buttons {
        grid-template-columns: 1fr;
      }
      
      .social-links {
        gap: 1rem;
      }
    }

    /* Plyr customization */
    .plyr--video .plyr__control--overlaid {
      background: var(--primary);
      padding: 1.8rem;
    }
    
    .plyr--video .plyr__control--overlaid:hover {
      background: var(--primary-hover);
    }
    
    .plyr--video .plyr__control:hover,
    .plyr--video .plyr__control[aria-expanded="true"] {
      background: var(--primary-hover);
    }
    
    .plyr__control.plyr__tab-focus {
      box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.6);
    }
    
    .plyr--full-ui input[type="range"] {
      color: var(--primary);
    }
    
    .plyr__menu__container .plyr__control[role="menuitemradio"][aria-checked="true"]::before {
      background: var(--primary);
    }
  </style>
</head>
<body class="dark">
  <header>
    <div class="logo">
      <i class="fas fa-film"></i>
      <span>Filmax</span>
    </div>
  </header>

 <div class="container">
    <div class="player-container">
        <video src="{src}" class="player" playsinline controls></video>

        <div class="file-name-container">
            <div id="file-name">{file_name}</div>
        </div>

        <div class="action-buttons">
            <a href="{src}" class="action-btn download-btn" target="_blank">
                <i class="fas fa-download"></i>
                Download
            </a>
            
            <button class="action-btn copy-btn" id="copy-link-btn">
                <i class="fas fa-copy"></i>
                Copy Link
            </button>

            <button class="action-btn vlc-btn" onclick="vlc_player()">
            <svg class="player-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">
                <path d="M12.881,10.657l1.714-1.714c0.878-0.878,0.878-2.304,0-3.182c-0.878-0.878-2.304-0.878-3.182,0L12,4.496l-1.414-1.414
                c-0.878-0.878-2.304-0.878-3.182,0c-0.878,0.878-0.878,2.304,0,3.182l1.714,1.714L4,16h16L12.881,10.657z M2,2h20v2H2V2z"/>
            </svg>
            Play in VLC
            </button>

            <button class="action-btn mx-btn" onclick="mx_player()">
            <svg class="player-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">
                <path d="M12,2C6.487,2,2,6.487,2,12s4.487,10,10,10s10-4.487,10-10S17.513,2,12,2z M12,20c-4.414,0-8-3.586-8-8s3.586-8,8-8
                s8,3.586,8,8S16.414,20,12,20z"/>
                <polygon points="15,16 9,16 9,8 15,8"/>
            </svg>
            Play in MX Player
            </button>

            <button class="action-btn playit-btn" onclick="playit_player()">
            <svg class="player-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">
                <path d="M8,5.14V19.14L19,12.14L8,5.14z"/>
            </svg>
            Play in PlayIt
            </button>
        </div>
       
            </a>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="toast" id="copy-toast">Link copied to clipboard!</div>

  <footer>
    <p>🎥 Trouble playing? Try <strong>Download</strong> or external players for best experience.</p>
  </footer>
<script src="https://cdn.plyr.io/3.7.8/plyr.js"></script>
<script src="https://Silicon-Developer.github.io/Resources/silicon.js"></script>

</body>
</html>
"""

async def media_watch(message_id):
    media_msg = await temp.BOT.get_messages(BIN_CHANNEL, message_id)
    media = getattr(media_msg, media_msg.media.value, None)
    src = urllib.parse.urljoin(URL, f'download/{message_id}')
    tag = media.mime_type.split('/')[0].strip()
    if tag == 'video':
        html_ = watch_tmplt.replace('{file_name}', media.file_name).replace('{src}', src)
    else:
        html_ = '<h1>This is not streamable file</h1>'
    return html_
