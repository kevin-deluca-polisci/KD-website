# Kevin DeLuca Academic Website

A clean, maintainable academic website built with Hugo and hosted on GitHub Pages.

## How to Update Your Site

### Adding a Publication

1. Open `data/publications.csv` in Excel or Google Sheets
2. Add a new row with:
   - Title: Full title of the paper
   - Year: Publication year
   - Journal: Journal name
   - Volume: Volume number (optional)
   - Issue: Issue number (optional)
   - Pages: Page numbers (optional)
   - DOI: Link to DOI or publisher page (optional)
   - PDF: Link to PDF in your website (optional)
   - Authors: Author names (Author1, Author2, Author3 columns)
3. Save the file
4. Commit and push to GitHub
5. The site rebuilds automatically within seconds

### Adding a Media Mention

1. Open `data/media.csv` in Excel or Google Sheets
2. Add a new row with:
   - Title: Title of the article, quote, or appearance
   - Type: "Quote", "Article", "Testimony", etc.
   - Outlet: Where it appeared (NYT, Atlantic, etc.)
   - Date: Date of publication (YYYY-MM-DD format)
   - URL: Link to the article
   - Description: Brief description of what it was about
3. Save the file
4. Commit and push to GitHub
5. The site rebuilds automatically

### Editing Your Bio

1. Open `content/_index.md` in any text editor
2. Edit the text under the `---` lines
3. Keep the title as "Kevin DeLuca"
4. Save and push to GitHub
5. Changes appear within seconds

### Updating Menu Links

To change your Substack URL (or any menu links):
1. Open `config.toml`
2. Find the section with menu items
3. Update the URL for "Blog" or any other link
4. Save and push to GitHub

## Adding Your Photo

1. Add your photo as `static/images/profile.jpg`
2. You'll need to modify the theme template to display it (I can help with this)

## How to Use GitHub Desktop (Desktop Sync)

1. Install [GitHub Desktop](https://desktop.github.com)
2. Open it and sign in with your GitHub account
3. Click "File" → "Clone Repository"
4. Select your `kevin-website` repository
5. Choose a folder on your computer (like `Documents/kevin-website`)
6. Now whenever you edit the files in that folder and save them, GitHub Desktop will show the changes
7. Add a summary (e.g., "Updated publications") and click "Commit to main"
8. Click "Push origin" to upload to GitHub
9. The site rebuilds automatically

Or use GitHub's web interface anytime:
1. Go to github.com/your-username/kevin-website
2. Navigate to any file
3. Click the pencil icon to edit
4. Save changes with a commit message
5. Site rebuilds automatically

## File Structure

```
kevin-website/
├── config.toml              # Site configuration
├── content/
│   ├── _index.md            # Your home page/bio
│   ├── research/
│   │   └── _index.md        # Research page (auto-generated from CSV)
│   └── media/
│       └── _index.md        # Media page (auto-generated from CSV)
├── data/
│   ├── publications.csv     # Your publications data
│   └── media.csv            # Your media appearances data
└── static/
    └── images/              # Store photos here
```

## Tips

- **CSV files**: You can edit them in Excel, Google Sheets, or any text editor
- **Dates**: For media.csv, use YYYY-MM-DD format (e.g., 2025-01-15)
- **Links**: All URLs should include `http://` or `https://`
- **Names**: For multiple authors, list them in Author1, Author2, Author3 columns
- **No line breaks in CSV**: Keep titles and descriptions on one line

## Need Help?

If you get stuck, you can:
- Edit files directly in GitHub's web interface (no software needed)
- Use GitHub Desktop for syncing to your computer
- Keep the CSV files open in Excel and edit them there—just save and sync
