<?php

declare(strict_types=1);

namespace OCA\ContentSearch\Search;

use OCP\DB\QueryBuilder\IQueryBuilder;
use OCP\Files\IRootFolder;
use OCP\IDBConnection;
use OCP\IURLGenerator;
use OCP\IUser;
use OCP\Search\IProvider;
use OCP\Search\ISearchQuery;
use OCP\Search\SearchResult;
use OCP\Search\SearchResultEntry;

class SearchProvider implements IProvider {
	// Rank normalization 1 divides by 1 + log(length), so long documents don't win on size alone.
	// Recency: up to 30% extra for a file changed today, about 15% after a year.
	private const SQL = <<<'SQL'
		WITH q AS (SELECT websearch_to_tsquery('dutch', ?) AS q),
		hit AS (
			SELECT c.fileid, c.content,
			       ts_rank(c.tsv, q.q, 1)
			       * (1 + 0.3 / (1 + greatest(extract(epoch FROM now()) - f.mtime, 0) / 31557600.0)) AS rank
			FROM *PREFIX*contentsearch c
			JOIN *PREFIX*filecache f ON f.fileid = c.fileid, q
			WHERE c.tsv @@ q.q
			ORDER BY rank DESC, c.fileid
			LIMIT ? OFFSET ?
		)
		SELECT hit.fileid,
		       ts_headline('dutch', left(hit.content, 100000), q.q,
		                   'MaxWords=20, MinWords=8, StartSel=«, StopSel=»') AS snippet
		FROM hit, q
		ORDER BY hit.rank DESC, hit.fileid
		SQL;

	public function __construct(
		private IDBConnection $db,
		private IRootFolder $rootFolder,
		private IURLGenerator $url,
	) {
	}

	public function getId(): string {
		return 'contentsearch';
	}

	public function getName(): string {
		return 'Inhoud';
	}

	public function getOrder(string $route, array $routeParameters): int {
		return 10;
	}

	public function search(IUser $user, ISearchQuery $query): SearchResult {
		$term = trim($query->getTerm());
		if (mb_strlen($term) < 3) {
			return SearchResult::complete($this->getName(), []);
		}
		$limit = $query->getLimit();
		$offset = (int)($query->getCursor() ?? 0);

		$rows = $this->db->executeQuery(
			self::SQL,
			[$term, $limit, $offset],
			[IQueryBuilder::PARAM_STR, IQueryBuilder::PARAM_INT, IQueryBuilder::PARAM_INT],
		)->fetchAll();

		$userFolder = $this->rootFolder->getUserFolder($user->getUID());
		$entries = [];
		foreach ($rows as $row) {
			// Not owned by or shared with this user: skip
			$node = $userFolder->getFirstNodeById((int)$row['fileid']);
			if ($node === null) {
				continue;
			}
			$entries[] = new SearchResultEntry(
				$this->url->linkToRouteAbsolute('core.Preview.getPreviewByFileId', ['x' => 32, 'y' => 32, 'fileId' => $node->getId()]),
				$node->getName(),
				trim(preg_replace('/\s+/u', ' ', (string)$row['snippet'])),
				$this->url->linkToRouteAbsolute('files.View.showFile', ['fileid' => $node->getId()]),
				'icon-filetype-file',
			);
		}

		if (count($rows) < $limit) {
			return SearchResult::complete($this->getName(), $entries);
		}
		return SearchResult::paginated($this->getName(), $entries, $offset + $limit);
	}
}
